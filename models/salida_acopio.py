# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
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
            # 1. Sincronizar datos actualizados al lote (CRETIB, manejo)
            self._sync_lot_data()
            # 2. Crear manifiesto PRIMERO (para tener el ID al crear picking)
            manifiesto = self._create_manifiesto_salida()
            # 3. Crear picking (el related en stock.move.line resolverá el manifiesto)
            picking = self._create_stock_picking()
            # 4. Escribir en la salida
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

    def _sync_lot_data(self):
        """
        Sincroniza los valores editados en la línea hacia el lote, para que
        el historial de movimientos y reportes ambientales reflejen la
        clasificación y plan de manejo correctos.
        """
        for linea in self.linea_ids:
            if not linea.lote_id:
                continue
            lot_vals = {}
            lot = linea.lote_id
            # CRETIB
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
            # Tipo de manejo
            if 'tipo_manejo_id' in lot._fields and linea.tipo_manejo_id:
                lot_vals['tipo_manejo_id'] = linea.tipo_manejo_id.id
            if lot_vals:
                try:
                    lot.sudo().write(lot_vals)
                except Exception as e:
                    _logger.warning(f"No se pudo sincronizar datos al lote {lot.name}: {e}")

    def _create_stock_picking(self):
        location_acopio = self._get_location_acopio()
        location_customer = self.env.ref('stock.stock_location_customers')
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id.company_id', '=', self.company_id.id)
        ], limit=1)
        if not picking_type:
            raise UserError("No se encontró un tipo de operación de salida configurado.")
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
        for linea in self.linea_ids:
            move = self.env['stock.move'].create({
                'product_id': linea.producto_id.id,
                'product_uom_qty': linea.cantidad,
                'product_uom': linea.producto_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': location_acopio.id,
                'location_dest_id': location_customer.id,
                'company_id': self.company_id.id,
                'description_picking': f"Salida Acopio: {linea.producto_id.name}",
            })
            if linea.lote_id:
                self.env['stock.move.line'].create({
                    'move_id': move.id,
                    'product_id': linea.producto_id.id,
                    'lot_id': linea.lote_id.id,
                    'quantity': linea.cantidad,
                    'product_uom_id': linea.producto_id.uom_id.id,
                    'location_id': location_acopio.id,
                    'location_dest_id': location_customer.id,
                })
        picking.action_confirm()
        picking.action_assign()
        can_validate = all(
            move.move_line_ids
            for move in picking.move_ids
            if move.product_id.tracking in ('lot', 'serial')
        )
        if can_validate:
            picking.button_validate()
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

        # Crear residuos usando los valores capturados en la línea (NO del producto)
        for linea in self.linea_ids:
            residuo_vals = {
                'manifiesto_id': manifiesto.id,
                'product_id': linea.producto_id.id,
                'nombre_residuo': linea.nombre_residuo or linea.producto_id.name,
                'cantidad': linea.cantidad,
                'residue_type': linea.residue_type or False,
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
                'envase_tipo': linea.envase_tipo or False,
                'envase_cantidad': linea.envase_cantidad or 1,
                'envase_capacidad': linea.envase_capacidad or '',
                'packaging_id': linea.packaging_id.id if linea.packaging_id else False,
                'etiqueta_si': linea.etiqueta_si,
                'etiqueta_no': linea.etiqueta_no,
            }
            residuo = self.env['manifiesto.ambiental.residuo'].create(residuo_vals)
            if linea.lote_id:
                residuo.lot_id = linea.lote_id.id
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

    cantidad = fields.Float(
        string='Cantidad (kg)', required=True, digits=(12, 3)
    )

    stock_disponible = fields.Float(
        string='Stock Disponible',
        compute='_compute_stock_disponible',
        store=True,
    )

    # === Descripción del residuo ===
    nombre_residuo = fields.Char(
        string='Nombre del Residuo',
        help='Descripción del residuo (se usa en el manifiesto)'
    )

    residue_type = fields.Selection(
        RESIDUE_TYPE_SELECTION,
        string='Tipo de Residuo'
    )

    # === Clasificación CRETIB ===
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

    # === Envase ===
    envase_tipo = fields.Selection(
        ENVASE_TIPO_SELECTION,
        string='Tipo de Envase (Legacy)'
    )
    packaging_id = fields.Many2one('uom.uom', string='Embalaje')
    envase_cantidad = fields.Integer(string='Unidades', default=1)
    envase_capacidad = fields.Char(string='Capacidad')

    # === Plan de Manejo ===
    tipo_manejo_id = fields.Many2one(
        'residuo.tipo.manejo',
        string='Plan de Manejo'
    )

    # === Etiquetado ===
    etiqueta_si = fields.Boolean(string='Etiqueta - Sí', default=True)
    etiqueta_no = fields.Boolean(string='Etiqueta - No', default=False)

    def _get_location_acopio(self):
        return _find_location_acopio(self.env, self.env.company.id)

    def _get_lot_ids_in_acopio(self):
        """Retorna los IDs de lotes con stock > 0 en Acopio para el producto actual."""
        if not self.producto_id:
            return []
        location_acopio = self._get_location_acopio()
        if not location_acopio:
            return []
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.producto_id.id),
            ('location_id', '=', location_acopio.id),
            ('quantity', '>', 0),
        ])
        lot_ids = quants.filtered(lambda q: q.lot_id).mapped('lot_id').ids
        return lot_ids

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
        """Carga valores por defecto desde el producto."""
        prod = self.producto_id
        if not prod:
            return
        self.nombre_residuo = prod.name
        for f in ('clasificacion_corrosivo', 'clasificacion_reactivo',
                  'clasificacion_explosivo', 'clasificacion_toxico',
                  'clasificacion_inflamable', 'clasificacion_biologico'):
            if hasattr(prod, f):
                setattr(self, f, getattr(prod, f))
        if hasattr(prod, 'envase_tipo_default'):
            self.envase_tipo = prod.envase_tipo_default
        if hasattr(prod, 'envase_capacidad_default') and prod.envase_capacidad_default:
            self.envase_capacidad = str(prod.envase_capacidad_default)

    def _load_from_lot(self):
        """
        Carga los datos del residuo desde el lote y/o del residuo del manifiesto
        de entrada original (si existe), para evitar recapturar.
        """
        lot = self.lote_id
        if not lot:
            return

        # 1. Clasificación y plan de manejo desde el lote (si los tiene)
        cretib_fields = [
            'clasificacion_corrosivo', 'clasificacion_reactivo',
            'clasificacion_explosivo', 'clasificacion_toxico',
            'clasificacion_inflamable', 'clasificacion_biologico',
        ]
        for f in cretib_fields:
            if f in lot._fields:
                setattr(self, f, getattr(lot, f))
        if 'tipo_manejo_id' in lot._fields and lot.tipo_manejo_id:
            self.tipo_manejo_id = lot.tipo_manejo_id.id

        # 2. Datos del residuo del manifiesto de entrada (envase, cantidades, tipo)
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
            # CRETIB desde residuo si el lote no lo tuvo
            for f in cretib_fields:
                if not getattr(self, f) and getattr(residuo, f, False):
                    setattr(self, f, True)

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        self.lote_id = False
        self.cantidad = 0.0
        if not self.producto_id:
            return {'domain': {'lote_id': [('id', '=', False)]}}
        # Cargar defaults del producto
        self._load_from_product()
        lot_ids = self._get_lot_ids_in_acopio()
        return {'domain': {'lote_id': [('id', 'in', lot_ids)]}}

    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        if not self.lote_id or not self.producto_id:
            if not self.lote_id:
                self.cantidad = 0.0
            return
        # Calcular stock disponible
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
        # Precargar datos ambientales
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