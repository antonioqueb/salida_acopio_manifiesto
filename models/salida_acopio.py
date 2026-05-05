# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


def _find_location_acopio(env, company_id=None):
    """Busca la ubicación Acopio de forma flexible por complete_name."""
    domain = [
        ('complete_name', 'ilike', 'Acopio'),
        ('usage', '=', 'internal'),
    ]
    if company_id:
        loc = env['stock.location'].search(domain + [('company_id', '=', company_id)], limit=1)
        if loc:
            return loc
    return env['stock.location'].search(domain, limit=1)


ENVASE_TIPO_SELECTION = [
    ('tambor', 'Tambor'),
    ('contenedor', 'Contenedor'),
    ('tote', 'Tote'),
    ('tarima', 'Tarima'),
    ('saco', 'Saco'),
    ('caja', 'Caja'),
    ('bolsa', 'Bolsa'),
    ('tanque', 'Tanque'),
    ('otro', 'Otro'),
]

RESIDUE_TYPE_SELECTION = [
    ('rsu', 'RSU'),
    ('rme', 'RME'),
    ('rp', 'RP'),
]


class SalidaAcopio(models.Model):
    _name = 'salida.acopio'
    _description = 'Registro de Salida de Acopio'
    _order = 'fecha_salida desc'
    _rec_name = 'numero_referencia'

    numero_referencia = fields.Char(
        string='Número de Referencia',
        required=True,
        copy=False,
        readonly=True,
        default='/'
    )

    manifiesto_salida_id = fields.Many2one(
        'manifiesto.ambiental',
        string='Manifiesto de Salida Generado',
        readonly=True,
        help='Manifiesto ambiental generado para esta salida (SAI como generador)'
    )

    fecha_salida = fields.Datetime(
        string='Fecha de Salida',
        required=True,
        default=fields.Datetime.now
    )

    usuario_salida = fields.Many2one(
        'res.users',
        string='Usuario que Procesó la Salida',
        required=True,
        default=lambda self: self.env.user
    )

    transportista_id = fields.Many2one(
        'res.partner',
        string='Transportista',
        domain=[('is_company', '=', True)],
        required=True,
    )

    destinatario_id = fields.Many2one(
        'res.partner',
        string='Destinatario Final',
        domain=[('is_company', '=', True)],
        required=True,
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('done', 'Realizada'),
        ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', required=True)

    picking_id = fields.Many2one(
        'stock.picking',
        string='Transferencia de Inventario',
        readonly=True,
    )

    linea_ids = fields.One2many(
        'salida.acopio.linea',
        'salida_id',
        string='Líneas de Salida'
    )

    total_residuos = fields.Integer(
        string='Total de Residuos',
        compute='_compute_totales',
        store=True
    )

    cantidad_total = fields.Float(
        string='Cantidad Total (kg)',
        compute='_compute_totales',
        store=True
    )

    observaciones = fields.Text(string='Observaciones')

    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('numero_referencia', '/') == '/':
                if vals.get('fecha_salida'):
                    if isinstance(vals['fecha_salida'], str):
                        fecha_utc = fields.Datetime.from_string(vals['fecha_salida'])
                    else:
                        fecha_utc = vals['fecha_salida']
                    fecha_local = fields.Datetime.context_timestamp(self, fecha_utc)
                else:
                    fecha_local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
                vals['numero_referencia'] = self.env['ir.sequence'].with_context(
                    ir_sequence_date=fecha_local.date()
                ).next_by_code('salida.acopio') or '/'
        return super().create(vals_list)

    @api.depends('linea_ids.cantidad')
    def _compute_totales(self):
        for record in self:
            record.total_residuos = len(record.linea_ids)
            record.cantidad_total = sum(record.linea_ids.mapped('cantidad'))

    def name_get(self):
        result = []
        for record in self:
            name = f"{record.numero_referencia}"
            if record.manifiesto_salida_id:
                name += f" - Manifiesto: {record.manifiesto_salida_id.numero_manifiesto}"
            result.append((record.id, name))
        return result

    def action_confirmar_salida(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError("Solo se pueden confirmar salidas en estado borrador.")
        if not self.linea_ids:
            raise UserError("No hay líneas de salida para procesar.")
        if not self.transportista_id:
            raise UserError("Debe seleccionar un transportista.")
        if not self.destinatario_id:
            raise UserError("Debe seleccionar un destinatario final.")

        # === VALIDACIÓN: lotes duplicados dentro de la misma salida ===
        self._validate_no_duplicates()

        # === VALIDACIÓN: lotes ya usados en otras salidas ===
        self._validate_lotes_no_usados_previamente()

        for linea in self.linea_ids:
            if linea.cantidad <= 0:
                raise UserError(
                    f"La cantidad del producto {linea.producto_id.name} debe ser mayor a cero."
                )
            if linea.cantidad > linea.stock_disponible:
                raise UserError(
                    f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                    f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg"
                )
        try:
            self._sync_lot_data()
            manifiesto = self._create_manifiesto_salida()
            picking = self._create_stock_picking()
            self.write({
                'state': 'done',
                'picking_id': picking.id,
                'manifiesto_salida_id': manifiesto.id,
            })
            _logger.info(f"Salida de acopio {self.numero_referencia} confirmada exitosamente")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Salida Realizada',
                    'message': f'La salida {self.numero_referencia} se realizó. Manifiesto: {manifiesto.numero_manifiesto}',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.error(f"Error al confirmar salida {self.numero_referencia}: {str(e)}")
            raise UserError(f"Error al realizar la salida: {str(e)}")

    def _validate_no_duplicates(self):
        """Valida que no haya lotes (o productos sin lote) duplicados en la misma salida."""
        self.ensure_one()
        seen = {}
        for linea in self.linea_ids:
            if linea.lote_id:
                key = ('lot', linea.lote_id.id)
                label = f"Producto: {linea.producto_id.name} / Lote: {linea.lote_id.name}"
            else:
                key = ('prod', linea.producto_id.id)
                label = f"Producto: {linea.producto_id.name} (sin lote)"
            if key in seen:
                raise UserError(
                    f"⚠️ Residuo duplicado en la salida:\n\n{label}\n\n"
                    f"Cada lote (o producto sin lote) solo puede aparecer una vez en la salida. "
                    f"Elimine la línea duplicada antes de confirmar."
                )
            seen[key] = True

    def _validate_lotes_no_usados_previamente(self):
        """
        Valida que los lotes seleccionados no hayan sido usados en otra salida
        ya realizada (state='done') o en otra salida en borrador del mismo día.
        """
        self.ensure_one()
        for linea in self.linea_ids:
            if not linea.lote_id:
                continue
            # Lotes en otras salidas YA REALIZADAS
            otras_done = self.env['salida.acopio.linea'].search([
                ('lote_id', '=', linea.lote_id.id),
                ('salida_id', '!=', self.id),
                ('salida_id.state', '=', 'done'),
            ], limit=1)
            if otras_done:
                raise UserError(
                    f"⚠️ Lote ya entregado:\n\n"
                    f"El lote '{linea.lote_id.name}' del producto '{linea.producto_id.name}' "
                    f"ya fue dado de salida previamente en la salida "
                    f"'{otras_done.salida_id.numero_referencia}'.\n\n"
                    f"No es posible volver a darle salida."
                )
            # Lotes en otras salidas EN BORRADOR (alerta de reserva doble)
            otras_draft = self.env['salida.acopio.linea'].search([
                ('lote_id', '=', linea.lote_id.id),
                ('salida_id', '!=', self.id),
                ('salida_id.state', '=', 'draft'),
            ], limit=1)
            if otras_draft:
                raise UserError(
                    f"⚠️ Lote reservado en otra salida:\n\n"
                    f"El lote '{linea.lote_id.name}' del producto '{linea.producto_id.name}' "
                    f"ya está incluido en la salida en borrador "
                    f"'{otras_draft.salida_id.numero_referencia}'.\n\n"
                    f"Cancele o procese primero esa salida, o elimine el lote de una de las dos."
                )

    def _sync_lot_data(self):
        for linea in self.linea_ids:
            if not linea.lote_id:
                continue
            lot_vals = {}
            lot = linea.lote_id
            cretib_map = {
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
            }
            for k, v in cretib_map.items():
                if k in lot._fields:
                    lot_vals[k] = v
            if 'tipo_manejo_id' in lot._fields and linea.tipo_manejo_id:
                lot_vals['tipo_manejo_id'] = linea.tipo_manejo_id.id
            if lot_vals:
                try:
                    lot.sudo().write(lot_vals)
                except Exception as e:
                    _logger.warning(f"No se pudo sincronizar datos al lote {lot.name}: {e}")

    def _build_move_description(self, linea):
        """Construye la descripción del move incluyendo CRETIB y plan de manejo."""
        parts = [linea.nombre_residuo or linea.producto_id.display_name]
        if linea.clasificaciones_cretib:
            parts.append(f"CRETIB: {linea.clasificaciones_cretib}")
        if linea.lote_id:
            parts.append(f"Lote: {linea.lote_id.name}")
        if linea.tipo_manejo_id:
            parts.append(f"Plan de Manejo: {linea.tipo_manejo_id.name}")
        return "\n".join(parts)

    def _create_stock_picking(self):
        location_acopio = self._get_location_acopio()
        location_customer = self.env.ref('stock.stock_location_customers')
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id.company_id', '=', self.company_id.id)
        ], limit=1)
        if not picking_type:
            raise UserError("No se encontró un tipo de operación de salida configurado.")

        # PASO 1: Crear el picking SIN moves
        _logger.info("[ACOPIO] PASO 1: creando picking vacío")
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': location_acopio.id,
            'location_dest_id': location_customer.id,
            'origin': f"Salida Acopio: {self.numero_referencia}",
            'move_type': 'direct',
            'company_id': self.company_id.id,
            'partner_id': self.destinatario_id.id,
            'salida_acopio_id': self.id,
        })
        _logger.info(f"[ACOPIO] Picking creado: {picking.id} - {picking.name}")

        # PASO 2: Crear cada move INDIVIDUALMENTE con CRETIB y vínculo a la línea
        moves_created = []
        for idx, linea in enumerate(self.linea_ids, start=1):
            _logger.info(f"[ACOPIO] PASO 2.{idx}: creando move para {linea.producto_id.name}")
            move_vals = {
                'product_id': linea.producto_id.id,
                'product_uom_qty': linea.cantidad,
                'product_uom': linea.producto_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': location_acopio.id,
                'location_dest_id': location_customer.id,
                'company_id': self.company_id.id,
                'description_picking': self._build_move_description(linea),
                'salida_acopio_linea_id': linea.id,
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
            }
            move = self.env['stock.move'].create(move_vals)
            _logger.info(f"[ACOPIO] Move creado: {move.id}")
            moves_created.append((move, linea))

        # PASO 3: Confirmar y reservar
        _logger.info("[ACOPIO] PASO 3: action_confirm + action_assign")
        picking.action_confirm()
        picking.action_assign()

        # PASO 4: Asignar lotes y cantidades a las move_lines
        for idx, (move, linea) in enumerate(moves_created, start=1):
            _logger.info(f"[ACOPIO] PASO 4.{idx}: asignando lote/cantidad a move {move.id}")
            if linea.lote_id:
                move.move_line_ids.unlink()
                self.env['stock.move.line'].create({
                    'move_id': move.id,
                    'picking_id': picking.id,
                    'product_id': linea.producto_id.id,
                    'lot_id': linea.lote_id.id,
                    'quantity': linea.cantidad,
                    'product_uom_id': linea.producto_id.uom_id.id,
                    'location_id': location_acopio.id,
                    'location_dest_id': location_customer.id,
                })
            else:
                if move.move_line_ids:
                    move.move_line_ids[0].quantity = linea.cantidad
                else:
                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'picking_id': picking.id,
                        'product_id': linea.producto_id.id,
                        'quantity': linea.cantidad,
                        'product_uom_id': linea.producto_id.uom_id.id,
                        'location_id': location_acopio.id,
                        'location_dest_id': location_customer.id,
                    })

        # PASO 4.5: Re-escribir CRETIB en los moves por si el flujo de assign los pisó
        for move, linea in moves_created:
            move.write({
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
                'description_picking': self._build_move_description(linea),
            })

        # PASO 5: Marcar picked (Odoo 17+)
        _logger.info("[ACOPIO] PASO 5: marcando moves como picked")
        if 'picked' in self.env['stock.move']._fields:
            picking.move_ids.write({'picked': True})

        # PASO 6: Validar
        _logger.info("[ACOPIO] PASO 6: button_validate")
        try:
            result = picking.with_context(
                skip_backorder=True,
                picking_ids_not_to_backorder=picking.ids,
                skip_immediate=True,
            ).button_validate()
            _logger.info(f"[ACOPIO] button_validate result: {result}")
        except Exception as e:
            _logger.warning(f"[ACOPIO] button_validate lanzó excepción: {e}")

        _logger.info(f"[ACOPIO] Picking final state: {picking.state}")
        return picking

    def _get_or_create_sai_partner(self):
        sai_partner = self.env['res.partner'].search([
            ('name', 'ilike', 'SAI'),
            ('is_company', '=', True),
            ('es_generador', '=', True)
        ], limit=1)
        if not sai_partner:
            sai_partner = self.env['res.partner'].create({
                'name': self.company_id.name or 'SAI',
                'is_company': True,
                'es_generador': True,
                'numero_registro_ambiental': self.company_id.vat or '',
                'street': self.company_id.street or '',
                'street2': self.company_id.street2 or '',
                'city': self.company_id.city or '',
                'state_id': self.company_id.state_id.id if self.company_id.state_id else False,
                'zip': self.company_id.zip or '',
                'phone': self.company_id.phone or '',
                'email': self.company_id.email or '',
            })
            _logger.info(f"Partner SAI creado: {sai_partner.name}")
        return sai_partner

    def _create_manifiesto_salida(self):
        _logger.info("=== INICIO CREACIÓN MANIFIESTO DE SALIDA ===")
        sai_partner = self._get_or_create_sai_partner()
        manifiesto_vals = {
            'tipo_manifiesto': 'salida',
            'numero_manifiesto': self.numero_referencia,
            'generador_id': sai_partner.id,
            'generador_nombre': sai_partner.name,
            'numero_registro_ambiental': sai_partner.numero_registro_ambiental or sai_partner.vat or '',
            'generador_fecha': self.fecha_salida.date() if self.fecha_salida else fields.Date.context_today(self),
            'transportista_id': self.transportista_id.id,
            'transportista_nombre': self.transportista_id.name or '',
            'transportista_codigo_postal': self.transportista_id.zip or '',
            'transportista_calle': self.transportista_id.street or '',
            'transportista_num_ext': getattr(self.transportista_id, 'street_number', '') or '',
            'transportista_num_int': getattr(self.transportista_id, 'street_number2', '') or '',
            'transportista_colonia': self.transportista_id.street2 or '',
            'transportista_municipio': self.transportista_id.city or '',
            'transportista_estado': self.transportista_id.state_id.name if self.transportista_id.state_id else '',
            'transportista_telefono': self.transportista_id.phone or '',
            'transportista_email': self.transportista_id.email or '',
            'numero_autorizacion_semarnat': getattr(self.transportista_id, 'numero_autorizacion_semarnat', '') or '',
            'numero_permiso_sct': getattr(self.transportista_id, 'numero_permiso_sct', '') or '',
            'tipo_vehiculo': getattr(self.transportista_id, 'tipo_vehiculo', '') or '',
            'numero_placa': getattr(self.transportista_id, 'numero_placa', '') or '',
            'transportista_responsable_nombre': '',
            'transportista_fecha': self.fecha_salida.date() if self.fecha_salida else fields.Date.context_today(self),
            'destinatario_id': self.destinatario_id.id,
            'destinatario_nombre': self.destinatario_id.name or '',
            'destinatario_codigo_postal': self.destinatario_id.zip or '',
            'destinatario_calle': self.destinatario_id.street or '',
            'destinatario_num_ext': getattr(self.destinatario_id, 'street_number', '') or '',
            'destinatario_num_int': getattr(self.destinatario_id, 'street_number2', '') or '',
            'destinatario_colonia': self.destinatario_id.street2 or '',
            'destinatario_municipio': self.destinatario_id.city or '',
            'destinatario_estado': self.destinatario_id.state_id.name if self.destinatario_id.state_id else '',
            'destinatario_telefono': self.destinatario_id.phone or '',
            'destinatario_email': self.destinatario_id.email or '',
            'numero_autorizacion_semarnat_destinatario': getattr(self.destinatario_id, 'numero_autorizacion_semarnat', '') or '',
            'instrucciones_especiales': self.observaciones or '',
            'state': 'confirmed',
            'company_id': self.company_id.id,
        }
        manifiesto = self.env['manifiesto.ambiental'].create(manifiesto_vals)
        _logger.info(f"✅ Manifiesto creado: {manifiesto.numero_manifiesto} (tipo: salida)")

        for linea in self.linea_ids:
            residuo_vals = {
                'manifiesto_id': manifiesto.id,
                'product_id': linea.producto_id.id,
                'lot_id': linea.lote_id.id if linea.lote_id else False,
                'nombre_residuo': linea.nombre_residuo or linea.producto_id.name,
                'cantidad': linea.cantidad,
                'residue_type': linea.residue_type or False,
                'envase_tipo': linea.envase_tipo or False,
                'envase_cantidad': linea.envase_cantidad or 1,
                'envase_capacidad': linea.envase_capacidad or '',
                'packaging_id': linea.packaging_id.id if linea.packaging_id else False,
                'etiqueta_si': linea.etiqueta_si,
                'etiqueta_no': linea.etiqueta_no,
            }
            residuo = self.env['manifiesto.ambiental.residuo'].create(residuo_vals)
            # Forzar CRETIB DESPUÉS del create para sobrescribir cualquier
            # onchange/compute disparado por lot_id que pudiera resetear las clasificaciones
            residuo.write({
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
            })
        _logger.info(f"🎉 FIN CREACIÓN MANIFIESTO: {manifiesto.numero_manifiesto}")
        return manifiesto

    def _get_location_acopio(self):
        location = _find_location_acopio(self.env, self.company_id.id)
        if not location:
            raise UserError(
                "No se encontró una ubicación de tipo interno que contenga 'Acopio' en su nombre. "
                "Verifique que exista en Inventario → Configuración → Ubicaciones."
            )
        return location

    def action_cancelar(self):
        self.ensure_one()
        if self.state == 'done':
            raise UserError("No se puede cancelar una salida ya realizada.")
        self.state = 'cancel'

    def action_view_picking(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError("No hay transferencia de inventario asociada.")
        return {
            'name': 'Transferencia de Inventario',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
            'target': 'current',
        }

    def action_view_manifiesto(self):
        self.ensure_one()
        if not self.manifiesto_salida_id:
            raise UserError("No hay manifiesto de salida asociado.")
        return {
            'name': f'Manifiesto de Salida - {self.manifiesto_salida_id.numero_manifiesto}',
            'type': 'ir.actions.act_window',
            'res_model': 'manifiesto.ambiental',
            'view_mode': 'form',
            'res_id': self.manifiesto_salida_id.id,
            'target': 'current',
        }


class SalidaAcopioLinea(models.Model):
    _name = 'salida.acopio.linea'
    _description = 'Línea de Salida de Acopio'

    salida_id = fields.Many2one(
        'salida.acopio', string='Salida de Acopio',
        required=True, ondelete='cascade'
    )

    producto_id = fields.Many2one(
        'product.product', string='Producto/Residuo', required=True,
    )

    lote_id = fields.Many2one(
        'stock.lot', string='Lote',
    )

    available_lot_ids = fields.Many2many(
        'stock.lot',
        string='Lotes Disponibles',
        compute='_compute_available_lot_ids',
        help='Lotes con stock en Acopio, excluyendo los ya seleccionados en esta salida y los ya entregados en otras salidas'
    )

    available_product_ids = fields.Many2many(
        'product.product',
        string='Productos Disponibles',
        compute='_compute_available_product_ids',
        help='Productos con stock disponible en la ubicación Acopio'
    )

    cantidad = fields.Float(
        string='Cantidad (kg)', required=True, digits=(12, 3)
    )

    stock_disponible = fields.Float(
        string='Stock Disponible',
        compute='_compute_stock_disponible',
        store=True,
    )

    nombre_residuo = fields.Char(
        string='Nombre del Residuo',
        help='Descripción del residuo (se usa en el manifiesto)'
    )

    residue_type = fields.Selection(
        RESIDUE_TYPE_SELECTION,
        string='Tipo de Residuo'
    )

    clasificacion_corrosivo = fields.Boolean(string='Corrosivo (C)')
    clasificacion_reactivo = fields.Boolean(string='Reactivo (R)')
    clasificacion_explosivo = fields.Boolean(string='Explosivo (E)')
    clasificacion_toxico = fields.Boolean(string='Tóxico (T)')
    clasificacion_inflamable = fields.Boolean(string='Inflamable (I)')
    clasificacion_biologico = fields.Boolean(string='Biológico (B)')

    clasificaciones_cretib = fields.Char(
        string='Clasificaciones CRETIB',
        compute='_compute_clasificaciones_cretib',
        store=True,
    )

    envase_tipo = fields.Selection(
        ENVASE_TIPO_SELECTION,
        string='Tipo de Envase (Legacy)'
    )
    packaging_id = fields.Many2one('uom.uom', string='Embalaje')
    envase_cantidad = fields.Integer(string='Unidades', default=1)
    envase_capacidad = fields.Char(string='Capacidad')

    tipo_manejo_id = fields.Many2one(
        'residuo.tipo.manejo',
        string='Plan de Manejo'
    )

    etiqueta_si = fields.Boolean(string='Etiqueta - Sí', default=True)
    etiqueta_no = fields.Boolean(string='Etiqueta - No', default=False)

    def _get_location_acopio(self):
        return _find_location_acopio(self.env, self.env.company.id)

    def _get_lots_with_stock_in_acopio(self):
        """Retorna recordset de lotes con stock > 0 en Acopio para el producto actual."""
        if not self.producto_id:
            return self.env['stock.lot']
        location_acopio = self._get_location_acopio()
        if not location_acopio:
            return self.env['stock.lot']
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.producto_id.id),
            ('location_id', '=', location_acopio.id),
            ('quantity', '>', 0),
        ])
        return quants.mapped('lot_id')

    @api.depends('salida_id')
    def _compute_available_product_ids(self):
        """Productos con quants en Acopio (cantidad > 0)."""
        location_acopio = None
        for record in self:
            if not location_acopio:
                location_acopio = record._get_location_acopio()
            if not location_acopio:
                record.available_product_ids = [(5, 0, 0)]
                continue
            quants = record.env['stock.quant'].search([
                ('location_id', '=', location_acopio.id),
                ('quantity', '>', 0),
            ])
            product_ids = quants.mapped('product_id').ids
            if record.producto_id and record.producto_id.id not in product_ids:
                product_ids = product_ids + [record.producto_id.id]
            record.available_product_ids = [(6, 0, product_ids)]

    @api.depends('producto_id', 'salida_id.linea_ids.lote_id', 'salida_id.linea_ids.producto_id')
    def _compute_available_lot_ids(self):
        for record in self:
            if not record.producto_id:
                record.available_lot_ids = [(5, 0, 0)]
                continue

            stock_lots = record._get_lots_with_stock_in_acopio()
            available_ids = set(stock_lots.ids)

            if record.salida_id:
                used_in_same_salida = record.salida_id.linea_ids.filtered(
                    lambda l: l.id != record.id and l.lote_id
                ).mapped('lote_id').ids
                available_ids -= set(used_in_same_salida)

            if available_ids:
                ya_usados = record.env['salida.acopio.linea'].search([
                    ('lote_id', 'in', list(available_ids)),
                    ('salida_id', '!=', record.salida_id.id if record.salida_id else False),
                    ('salida_id.state', 'in', ('draft', 'done')),
                ]).mapped('lote_id').ids
                available_ids -= set(ya_usados)

            if record.lote_id:
                available_ids.add(record.lote_id.id)

            record.available_lot_ids = [(6, 0, list(available_ids))]

    @api.depends('producto_id', 'lote_id')
    def _compute_stock_disponible(self):
        for record in self:
            if not record.producto_id:
                record.stock_disponible = 0.0
                continue
            location_acopio = record._get_location_acopio()
            if not location_acopio:
                record.stock_disponible = 0.0
                continue
            domain = [
                ('product_id', '=', record.producto_id.id),
                ('location_id', '=', location_acopio.id),
                ('quantity', '>', 0),
            ]
            if record.lote_id:
                domain.append(('lot_id', '=', record.lote_id.id))
            quants = self.env['stock.quant'].search(domain)
            record.stock_disponible = sum(quants.mapped('quantity'))

    @api.depends(
        'clasificacion_corrosivo', 'clasificacion_reactivo', 'clasificacion_explosivo',
        'clasificacion_toxico', 'clasificacion_inflamable', 'clasificacion_biologico'
    )
    def _compute_clasificaciones_cretib(self):
        for record in self:
            tags = []
            if record.clasificacion_corrosivo: tags.append('C')
            if record.clasificacion_reactivo: tags.append('R')
            if record.clasificacion_explosivo: tags.append('E')
            if record.clasificacion_toxico: tags.append('T')
            if record.clasificacion_inflamable: tags.append('I')
            if record.clasificacion_biologico: tags.append('B')
            record.clasificaciones_cretib = ', '.join(tags)

    def _load_from_product(self):
        prod = self.producto_id
        if not prod:
            return
        if not self.nombre_residuo:
            self.nombre_residuo = prod.name
        # Solo precargar CRETIB si el producto los tiene en True (no pisar con False)
        for f in ('clasificacion_corrosivo', 'clasificacion_reactivo',
                  'clasificacion_explosivo', 'clasificacion_toxico',
                  'clasificacion_inflamable', 'clasificacion_biologico'):
            if hasattr(prod, f) and getattr(prod, f):
                setattr(self, f, True)
        if hasattr(prod, 'envase_tipo_default') and prod.envase_tipo_default:
            self.envase_tipo = prod.envase_tipo_default
        if hasattr(prod, 'envase_capacidad_default') and prod.envase_capacidad_default:
            self.envase_capacidad = str(prod.envase_capacidad_default)

    def _load_from_lot(self):
        lot = self.lote_id
        if not lot:
            return

        cretib_fields = [
            'clasificacion_corrosivo', 'clasificacion_reactivo',
            'clasificacion_explosivo', 'clasificacion_toxico',
            'clasificacion_inflamable', 'clasificacion_biologico',
        ]
        for f in cretib_fields:
            if f in lot._fields and getattr(lot, f):
                setattr(self, f, True)
        if 'tipo_manejo_id' in lot._fields and lot.tipo_manejo_id:
            self.tipo_manejo_id = lot.tipo_manejo_id.id

        residuo = self.env['manifiesto.ambiental.residuo'].search([
            ('lot_id', '=', lot.id),
            ('manifiesto_id.tipo_manifiesto', '=', 'entrada'),
            ('manifiesto_id.is_current_version', '=', True),
        ], limit=1, order='id desc')
        if residuo:
            if not self.nombre_residuo:
                self.nombre_residuo = residuo.nombre_residuo
            self.residue_type = residuo.residue_type or False
            self.envase_tipo = residuo.envase_tipo or False
            self.envase_cantidad = residuo.envase_cantidad or 1
            self.envase_capacidad = residuo.envase_capacidad or ''
            self.packaging_id = residuo.packaging_id.id if residuo.packaging_id else False
            for f in cretib_fields:
                if not getattr(self, f) and getattr(residuo, f, False):
                    setattr(self, f, True)

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        self.lote_id = False
        self.cantidad = 0.0
        if not self.producto_id:
            return
        self._load_from_product()

    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        if not self.lote_id or not self.producto_id:
            if not self.lote_id:
                self.cantidad = 0.0
            return

        if self.salida_id:
            lineas_con_lote = self.salida_id.linea_ids.filtered(
                lambda l: l.lote_id and l.lote_id.id == self.lote_id.id
            )
            if len(lineas_con_lote) > 1:
                lote_name = self.lote_id.name
                self.lote_id = False
                self.cantidad = 0.0
                return {
                    'warning': {
                        'title': '⚠️ Lote duplicado',
                        'message': (
                            f'El lote "{lote_name}" ya está seleccionado en otra línea '
                            f'de esta misma salida. Cada lote solo puede aparecer una vez.'
                        )
                    }
                }

        otras_done = self.env['salida.acopio.linea'].search([
            ('lote_id', '=', self.lote_id.id),
            ('salida_id', '!=', self.salida_id.id if self.salida_id else False),
            ('salida_id.state', 'in', ('draft', 'done')),
        ], limit=1)
        if otras_done:
            estado = 'ya entregado en' if otras_done.salida_id.state == 'done' else 'reservado en borrador en'
            lote_name = self.lote_id.name
            ref = otras_done.salida_id.numero_referencia
            self.lote_id = False
            self.cantidad = 0.0
            return {
                'warning': {
                    'title': '⚠️ Lote no disponible',
                    'message': (
                        f'El lote "{lote_name}" está {estado} la salida "{ref}".\n\n'
                        f'No es posible volver a seleccionarlo.'
                    )
                }
            }

        location_acopio = self._get_location_acopio()
        if location_acopio:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', self.producto_id.id),
                ('location_id', '=', location_acopio.id),
                ('lot_id', '=', self.lote_id.id),
                ('quantity', '>', 0),
            ])
            disponible = sum(quants.mapped('quantity'))
            self.stock_disponible = disponible
            if disponible > 0 and self.cantidad == 0.0:
                self.cantidad = disponible
        self._load_from_lot()

    @api.onchange('etiqueta_si')
    def _onchange_etiqueta_si(self):
        if self.etiqueta_si:
            self.etiqueta_no = False

    @api.onchange('etiqueta_no')
    def _onchange_etiqueta_no(self):
        if self.etiqueta_no:
            self.etiqueta_si = False

    @api.constrains('cantidad', 'stock_disponible')
    def _check_cantidad_disponible(self):
        for record in self:
            if record.cantidad > 0 and record.cantidad > record.stock_disponible:
                raise UserError(
                    f"La cantidad ({record.cantidad} kg) no puede ser mayor "
                    f"al stock disponible ({record.stock_disponible} kg) "
                    f"para {record.producto_id.name}"
                )

    @api.constrains('lote_id', 'producto_id', 'salida_id')
    def _check_lote_unico_en_salida(self):
        """Constraint dura: el mismo lote no puede repetirse en la misma salida."""
        for record in self:
            if not record.lote_id or not record.salida_id:
                continue
            duplicados = record.salida_id.linea_ids.filtered(
                lambda l: l != record
                and l.lote_id
                and l.lote_id.id == record.lote_id.id
            )
            if duplicados:
                raise ValidationError(
                    f"⚠️ El lote '{record.lote_id.name}' (producto '{record.producto_id.name}') "
                    f"ya está incluido en otra línea de esta salida. "
                    f"Cada lote solo puede aparecer una vez."
                )