## ./__init__.py
```py
# -*- coding: utf-8 -*-
from . import models
from . import wizard```

## ./__manifest__.py
```py
{
    'name': 'Salida Acopio Manifiesto',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Salida automática de residuos del inventario hacia disposición final con manifiestos de salida',
    'description': '''
        Salida Acopio para Manifiestos Ambientales
        ==========================================
        
        Este módulo permite realizar salidas automáticas de residuos del inventario
        hacia su disposición final, generando manifiestos donde SAI es el generador.
        
        Características principales:
        • Control de salidas desde ubicación "Acopio"
        • Generación automática de manifiestos de salida (SAI como generador)
        • Integración completa con el módulo de manifiestos
        • Trazabilidad completa de despachos
        • Menú raíz independiente para gestión completa
        
        Funcionalidades:
        • Salida automática desde inventario ubicación "Acopio"
        • Generación de manifiestos con SAI como generador
        • Selección de transportista y destinatario
        • Integración con transferencias de stock
        • Validaciones de negocio robustas
        • Historial completo de salidas
    ''',
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['base', 'stock', 'manifiesto_ambiental'],
    'data': [
        'security/ir.model.access.csv',
        'data/stock_data.xml',
        'reports/manifiesto_salida_report.xml',
        'wizard/salida_acopio_wizard_views.xml',
        'views/salida_acopio_views.xml',
        'views/salida_acopio_print_views.xml',
        'views/salida_acopio_menus.xml',
    ],
    'demo': [],
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}```

## ./data/stock_data.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Secuencia para salida de acopio con fecha -->
    <record id="seq_salida_acopio" model="ir.sequence">
        <field name="name">Salida de Acopio</field>
        <field name="code">salida.acopio</field>
        <field name="prefix">SAI-%(day)s%(month)s%(year)s-</field>
        <field name="padding">1</field>
        <field name="use_date_range">True</field>
        <field name="company_id" eval="False"/>
    </record>

</odoo>```

## ./models/__init__.py
```py
# -*- coding: utf-8 -*-
from . import salida_acopio
from . import salida_acopio_print
from . import stock_picking_inherit```

## ./models/salida_acopio_print.py
```py
# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError


class SalidaAcopioPrint(models.Model):
    _inherit = 'salida.acopio'

    def action_print_manifiesto_salida(self):
        """Imprime el manifiesto de salida usando el reporte específico de salida."""
        self.ensure_one()
        if not self.manifiesto_salida_id:
            raise UserError(_("No hay manifiesto de salida asociado a este registro."))
        return self.env.ref(
            'salida_acopio_manifiesto.action_report_manifiesto_salida'
        ).report_action(self.manifiesto_salida_id)```

## ./models/salida_acopio.py
```py
# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


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
            if linea.cantidad > linea.stock_disponible:
                raise UserError(
                    f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                    f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg"
                )
        try:
            picking = self._create_stock_picking()
            manifiesto = self._create_manifiesto_salida()
            self.write({'state': 'done', 'picking_id': picking.id, 'manifiesto_salida_id': manifiesto.id})
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
        _logger.info(f"✅ Manifiesto creado: {manifiesto.numero_manifiesto}")
        for linea in self.linea_ids:
            residuo = self.env['manifiesto.ambiental.residuo'].create({
                'manifiesto_id': manifiesto.id,
                'product_id': linea.producto_id.id,
                'nombre_residuo': linea.producto_id.name,
                'cantidad': linea.cantidad,
                'clasificacion_corrosivo': getattr(linea.producto_id, 'clasificacion_corrosivo', False),
                'clasificacion_reactivo': getattr(linea.producto_id, 'clasificacion_reactivo', False),
                'clasificacion_explosivo': getattr(linea.producto_id, 'clasificacion_explosivo', False),
                'clasificacion_toxico': getattr(linea.producto_id, 'clasificacion_toxico', False),
                'clasificacion_inflamable': getattr(linea.producto_id, 'clasificacion_inflamable', False),
                'clasificacion_biologico': getattr(linea.producto_id, 'clasificacion_biologico', False),
                'envase_tipo': getattr(linea.producto_id, 'envase_tipo_default', ''),
                'envase_capacidad': getattr(linea.producto_id, 'envase_capacidad_default', 0),
                'etiqueta_si': True,
                'etiqueta_no': False,
            })
            if linea.lote_id:
                residuo.lot_id = linea.lote_id.id
        _logger.info(f"🎉 FIN CREACIÓN MANIFIESTO: {manifiesto.numero_manifiesto}")
        return manifiesto

    def _get_location_acopio(self):
        location_acopio = self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        if not location_acopio:
            raise UserError("No se encontró la ubicación 'Acopio'.")
        return location_acopio

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

    lote_domain_ids = fields.Many2many(
        'stock.lot',
        'salida_acopio_linea_lote_domain_rel',
        'linea_id', 'lot_id',
        string='Lotes en Acopio',
        compute='_compute_lote_domain_ids',
        store=True,
    )

    cantidad = fields.Float(
        string='Cantidad (kg)', required=True, digits=(12, 3)
    )

    stock_disponible = fields.Float(
        string='Stock Disponible',
        compute='_compute_stock_disponible',
        store=True,
    )

    clasificaciones_cretib = fields.Char(
        string='Clasificaciones CRETIB',
        compute='_compute_clasificaciones_cretib',
        readonly=True
    )

    def _get_location_acopio(self):
        company = self.env.company
        _logger.info(f"[ACOPIO DEBUG FORM] Buscando ubicación Acopio para company_id={company.id} ({company.name})")
        location = self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', company.id)
        ], limit=1)
        if location:
            _logger.info(f"[ACOPIO DEBUG FORM] Ubicación encontrada: id={location.id}, complete_name={location.complete_name}")
        else:
            todas = self.env['stock.location'].search([('name', '=', 'Acopio')])
            _logger.warning(
                f"[ACOPIO DEBUG FORM] NO se encontró Acopio para company_id={company.id}. "
                f"Ubicaciones 'Acopio' existentes: {[(l.id, l.complete_name, l.company_id.id) for l in todas]}"
            )
        return location

    @api.depends('producto_id')
    def _compute_lote_domain_ids(self):
        for record in self:
            _logger.info(f"[ACOPIO DEBUG FORM] _compute_lote_domain_ids() para producto_id={record.producto_id.id if record.producto_id else None}")
            if not record.producto_id:
                record.lote_domain_ids = [(5, 0, 0)]
                continue
            location_acopio = record._get_location_acopio()
            if not location_acopio:
                record.lote_domain_ids = [(5, 0, 0)]
                continue

            # Todos los quants sin filtro para diagnóstico
            todos_quants = self.env['stock.quant'].search([
                ('product_id', '=', record.producto_id.id),
                ('location_id', '=', location_acopio.id),
            ])
            _logger.info(
                f"[ACOPIO DEBUG FORM] Todos los quants en Acopio para {record.producto_id.name} "
                f"(id={record.producto_id.id}): "
                f"{[(q.id, q.lot_id.name if q.lot_id else 'sin lote', q.quantity, q.reserved_quantity) for q in todos_quants]}"
            )

            quants = todos_quants.filtered(lambda q: q.quantity > 0 and q.lot_id)
            lot_ids = quants.mapped('lot_id').ids
            _logger.info(f"[ACOPIO DEBUG FORM] Quants filtrados (qty>0 y con lote): {[(q.lot_id.name, q.quantity) for q in quants]}")
            _logger.info(f"[ACOPIO DEBUG FORM] lot_ids resultantes: {lot_ids}")
            record.lote_domain_ids = [(6, 0, lot_ids)]
            _logger.info(f"[ACOPIO DEBUG FORM] lote_domain_ids asignados: {record.lote_domain_ids.ids}")

    @api.depends('producto_id', 'lote_id')
    def _compute_stock_disponible(self):
        for record in self:
            if not record.producto_id:
                record.stock_disponible = 0.0
                continue
            try:
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
            except Exception:
                record.stock_disponible = 0.0

    @api.depends('producto_id')
    def _compute_clasificaciones_cretib(self):
        for record in self:
            if record.producto_id and hasattr(record.producto_id, 'get_clasificaciones_cretib'):
                record.clasificaciones_cretib = record.producto_id.get_clasificaciones_cretib()
            else:
                record.clasificaciones_cretib = ''

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        _logger.info(f"[ACOPIO DEBUG FORM] _onchange_producto_id() producto={self.producto_id.name if self.producto_id else None}")
        self.lote_id = False
        self.cantidad = 0.0
        self._compute_lote_domain_ids()
        _logger.info(f"[ACOPIO DEBUG FORM] Después de _compute, lote_domain_ids.ids={self.lote_domain_ids.ids}")
        return {
            'domain': {
                'lote_id': [('id', 'in', self.lote_domain_ids.ids)]
            }
        }

    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        if not self.lote_id or not self.producto_id:
            if not self.lote_id:
                self.cantidad = 0.0
            return
        location_acopio = self._get_location_acopio()
        if not location_acopio:
            return
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.producto_id.id),
            ('location_id', '=', location_acopio.id),
            ('lot_id', '=', self.lote_id.id),
            ('quantity', '>', 0),
        ])
        disponible = sum(quants.mapped('quantity'))
        self.stock_disponible = disponible
        if disponible > 0:
            self.cantidad = disponible

    @api.constrains('cantidad', 'stock_disponible')
    def _check_cantidad_disponible(self):
        for record in self:
            if record.cantidad > 0 and record.cantidad > record.stock_disponible:
                raise UserError(
                    f"La cantidad ({record.cantidad} kg) no puede ser mayor "
                    f"al stock disponible ({record.stock_disponible} kg) "
                    f"para {record.producto_id.name}"
                )```

## ./models/stock_picking_inherit.py
```py
# -*- coding: utf-8 -*-
from odoo import models, fields


class StockPicking(models.Model):
    _inherit = 'stock.picking'
    
    salida_acopio_id = fields.Many2one(
        'salida.acopio',
        string='Salida de Acopio',
        help='Salida de acopio que generó esta transferencia'
    )
    
    es_salida_acopio = fields.Boolean(
        string='Es Salida de Acopio',
        compute='_compute_es_salida_acopio',
        help='Indica si esta transferencia fue generada por una salida de acopio'
    )

    def _compute_es_salida_acopio(self):
        for record in self:
            record.es_salida_acopio = bool(record.salida_acopio_id)```

## ./reports/manifiesto_salida_report.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- FORMATO DE PÁGINA A4 SIN MÁRGENES (SALIDA) -->
    <record id="paperformat_manifiesto_salida_sin_margen" model="report.paperformat">
        <field name="name">Manifiesto Salida</field>
        <field name="default" eval="False"/>
        <field name="format">A4</field>
        <field name="orientation">Portrait</field>
        <field name="margin_top">2</field>
        <field name="margin_bottom">10</field>
        <field name="margin_left">5</field>
        <field name="margin_right">5</field>
        <field name="header_line" eval="False"/>
        <field name="header_spacing">35</field>
        <field name="dpi">90</field>
    </record>

    <!-- ACCIÓN DEL REPORTE DE SALIDA -->
    <record id="action_report_manifiesto_salida" model="ir.actions.report">
        <field name="name">Manifiesto de Salida Report</field>
        <field name="model">manifiesto.ambiental</field>
        <field name="report_type">qweb-pdf</field>
        <field name="report_name">salida_acopio_manifiesto.manifiesto_salida_document</field>
        <field name="report_file">salida_acopio_manifiesto.manifiesto_salida_document</field>
        <!-- NO binding_model_id para que no aparezca en el menú Imprimir del manifiesto genérico -->
        <field name="paperformat_id" ref="salida_acopio_manifiesto.paperformat_manifiesto_salida_sin_margen"/>
    </record>

    <!-- PLANTILLA QWEB DEL REPORTE DE SALIDA (idéntica al de ingreso por ahora) -->
    <template id="manifiesto_salida_document">
        <t t-call="web.html_container">
            <t t-foreach="docs" t-as="doc">
                <t t-call="web.external_layout">
                    <div class="page">
                        <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
                        <style>
                            @page { margin: 5mm; size: A4; }

                            body, td, th, strong, .labelcell, .subcell {
                                font-family: "DejaVu Sans", Arial, "Liberation Sans", sans-serif !important;
                                font-size: 10.5px;
                                line-height: 1.15;
                            }
                            .page { margin-top: 2px !important; padding-top: 2px !important; }

                            .header, .o_company_document_layout .header, .company_address, .o_company_address,
                            .external_layout .header, .address, .o_report_layout_standard .header,
                            .o_report_layout_boxed .header, .o_report_layout_clean .header {
                                margin-bottom: 8px !important;
                                padding-bottom: 4px !important;
                                border-bottom: none !important;
                            }

                            .footer, .o_company_document_layout .footer {
                                display: none !important;
                                height: 0px !important;
                                padding: 0px !important;
                                margin: 0px !important;
                            }

                            .page-title {
                                text-align: center;
                                font-size: 12px;
                                font-weight: 700;
                                margin: 2px 0 4px;
                                font-family: "DejaVu Sans", Arial, sans-serif !important;
                            }

                            table { width: 100%; border-collapse: collapse; margin-bottom: 2px; page-break-inside: avoid; }
                            td, th { border: 1px solid #666; padding: 2px 4px; font-size: 10px; vertical-align: top; }

                            th, .header-table, .labelcell, .subcell { background: none !important; }
                            th, .header-table { font-weight: 700; text-align: center; }
                            .labelcell { font-weight: 700; font-size: 10.5px; }
                            .subcell { font-weight: 700; font-size: 9.8px; }

                            .no-border { border: none !important; }
                            .center-text { text-align: center; }

                            table.section-4 { width: 100%; border-collapse: collapse; table-layout: fixed; }

                            table.signature-section { width: 100%; border-collapse: separate; border-spacing: 0; margin-bottom: 2px; }
                            table.signature-section > tbody > tr > td { border: none !important; padding: 0; }
                            .signature-container { border: 1px solid #666; padding: 4px; }
                            .signature-text { margin: 0 0 4px 0; }
                            .signature-fields { display: table; width: 100%; margin-top: 2px; }
                            .signature-fields .cell { display: table-cell; vertical-align: top; padding-right: 8px; }
                            .signature-fields .cell:last-child { padding-right: 0; }

                            tr, td { page-break-inside: avoid; }
                        </style>

                        <!-- TÍTULO PRINCIPAL -->
                        <div class="page-title">
                            MANIFIESTO DE ENTREGA, TRANSPORTE Y RECEPCIÓN DE RESIDUOS PELIGROSOS (SALIDA)
                        </div>

                        <!-- ENCABEZADO 1-3 -->
                        <table>
                            <tr>
                                <td class="labelcell" style="width:45%">
                                    1. Núm. de registro ambiental: <span t-field="doc.numero_registro_ambiental"/>
                                </td>
                                <td class="labelcell" style="width:40%">
                                    2. Núm. de manifiesto: <span t-field="doc.numero_manifiesto"/>
                                </td>
                                <td class="labelcell" style="width:15%">
                                    3. Página: <span t-field="doc.pagina"/>
                                </td>
                            </tr>
                        </table>

                        <!-- 4. GENERADOR -->
                        <table class="section-4">
                            <colgroup>
                                <col style="width:15%"/>
                                <col style="width:25%"/>
                                <col style="width:40%"/>
                                <col style="width:10%"/>
                                <col style="width:10%"/>
                            </colgroup>

                            <tr>
                                <td class="labelcell" colspan="5">
                                    4. Nombre o razón social del generador: <span t-field="doc.generador_nombre"/>
                                </td>
                            </tr>

                            <tr>
                                <td class="subcell">Domicilio</td>
                                <td class="subcell">Código postal: <span t-field="doc.generador_codigo_postal"/></td>
                                <td class="subcell">Calle: <span t-field="doc.generador_calle"/></td>
                                <td class="subcell">Núm. Ext.: <span t-field="doc.generador_num_ext"/></td>
                                <td class="subcell">Núm. Int.: <span t-field="doc.generador_num_int"/></td>
                            </tr>

                            <tr>
                                <td class="subcell" colspan="2">
                                    Colonia: <span t-field="doc.generador_colonia"/>
                                </td>
                                <td class="subcell" colspan="1">
                                    Municipio o Delegación: <span t-field="doc.generador_municipio"/>
                                </td>
                                <td class="subcell" colspan="2">
                                    Estado: <span t-field="doc.generador_estado"/>
                                </td>
                            </tr>

                            <tr>
                                <td class="subcell" colspan="2">
                                    Teléfono: <span t-field="doc.generador_telefono"/>
                                </td>
                                <td class="subcell" colspan="3">
                                    Correo electrónico: <span t-field="doc.generador_email"/>
                                </td>
                            </tr>
                        </table>

                        <!-- 5. IDENTIFICACIÓN DE LOS RESIDUOS -->
                        <table>
                            <colgroup>
                                <col style="width:25%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                                <col style="width:12%"/>
                                <col style="width:10%"/>
                                <col style="width:10%"/>
                                <col style="width:3%"/>
                                <col style="width:3%"/>
                            </colgroup>

                            <tr>
                                <th colspan="13" class="header-table">5. Identificación de los residuos</th>
                            </tr>
                            <tr>
                                <th class="header-table">Nombre del residuo</th>
                                <th class="header-table" colspan="7">Clasificación</th>
                                <th class="header-table" colspan="2">Envase</th>
                                <th class="header-table">Cantidad (kg)</th>
                                <th class="header-table" colspan="2">Etiqueta</th>
                            </tr>
                            <tr>
                                <th class="header-table"></th>
                                <th class="header-table">C</th>
                                <th class="header-table">R</th>
                                <th class="header-table">E</th>
                                <th class="header-table">T</th>
                                <th class="header-table">I</th>
                                <th class="header-table">B</th>
                                <th class="header-table">M</th>
                                <th class="header-table">Embalaje</th>
                                <th class="header-table">Capacidad</th>
                                <th class="header-table"></th>
                                <th class="header-table">Sí</th>
                                <th class="header-table">No</th>
                            </tr>

                            <t t-foreach="doc.residuo_ids" t-as="residuo">
                                <tr>
                                    <td><span t-field="residuo.nombre_residuo"/></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_corrosivo">X</span></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_reactivo">X</span></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_explosivo">X</span></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_toxico">X</span></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_inflamable">X</span></td>
                                    <td class="center-text"><span t-if="residuo.clasificacion_biologico">X</span></td>
                                    <td class="center-text"></td>
                                    <td class="center-text">
                                        <span t-if="residuo.packaging_id" t-field="residuo.packaging_id.name"/>
                                        <span t-elif="residuo.envase_tipo" t-field="residuo.envase_tipo"/>
                                    </td>
                                    <td class="center-text"><span t-field="residuo.envase_capacidad"/></td>
                                    <td class="center-text"><span t-field="residuo.cantidad"/> kg</td>
                                    <td class="center-text"><span t-if="residuo.etiqueta_si">X</span></td>
                                    <td class="center-text"><span t-if="residuo.etiqueta_no">X</span></td>
                                </tr>
                            </t>

                            <t t-set="residuos_count" t-value="len(doc.residuo_ids)"/>
                            <t t-set="min_rows" t-value="18"/>
                            <t t-set="empty_rows" t-value="max(0, min_rows - residuos_count)"/>
                            <t t-foreach="range(empty_rows)" t-as="empty_row">
                                <tr style="height: 22px;">
                                    <td>&#160;</td><td>&#160;</td><td>&#160;</td><td>&#160;</td><td>&#160;</td>
                                    <td>&#160;</td><td>&#160;</td><td>&#160;</td><td>&#160;</td><td>&#160;</td>
                                    <td>&#160;</td><td>&#160;</td><td>&#160;</td>
                                </tr>
                            </t>
                        </table>

                        <!-- 6. INSTRUCCIONES ESPECIALES -->
                        <table>
                            <tr>
                                <td class="labelcell">6. Instrucciones especiales e información adicional para el manejo seguro:</td>
                            </tr>
                            <tr>
                                <td style="height: 28px; vertical-align: top;">
                                    <span t-field="doc.instrucciones_especiales"/>
                                </td>
                            </tr>
                        </table>

                        <!-- 7. DECLARACIÓN DEL GENERADOR -->
                        <table class="signature-section">
                            <tr>
                                <td>
                                    <div class="signature-container">
                                        <div class="signature-text">
                                            7. Declaración del generador: Declaro bajo protesta de decir verdad que el contenido de este lote está total y correctamente descrito mediante el número de manifiesto, nombre del residuo, características CRETIB, debidamente envasado y etiquetado y que se han previsto las condiciones de seguridad para su transporte por vía terrestre de acuerdo con la legislación vigente.
                                        </div>
                                        <div class="signature-fields">
                                            <div class="cell" style="width:40%;">
                                                <strong>Nombre y firma del responsable:</strong><br/>
                                                <span t-field="doc.generador_responsable_nombre"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Fecha:</strong><br/>
                                                <span t-field="doc.generador_fecha"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Sello:</strong><br/>
                                                <span t-field="doc.generador_sello"/>
                                            </div>
                                        </div>
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <!-- 8. TRANSPORTISTA -->
                        <table>
                            <tr>
                                <td colspan="4" class="labelcell">8. Nombre o razón social del transportista: <span t-field="doc.transportista_nombre"/></td>
                            </tr>
                            <tr>
                                <td class="subcell" style="width:25%;">Código postal: <span t-field="doc.transportista_codigo_postal"/></td>
                                <td class="subcell" style="width:25%;">Calle: <span t-field="doc.transportista_calle"/></td>
                                <td class="subcell" style="width:25%;">Núm. Ext.: <span t-field="doc.transportista_num_ext"/></td>
                                <td class="subcell" style="width:25%;">Núm. Int.: <span t-field="doc.transportista_num_int"/></td>
                            </tr>
                            <tr>
                                <td><strong>Colonia:</strong> <span t-field="doc.transportista_colonia"/></td>
                                <td><strong>Municipio o Delegación:</strong> <span t-field="doc.transportista_municipio"/></td>
                                <td colspan="2"><strong>Estado:</strong> <span t-field="doc.transportista_estado"/></td>
                            </tr>
                            <tr>
                                <td><strong>Teléfono:</strong> <span t-field="doc.transportista_telefono"/></td>
                                <td colspan="3"><strong>Correo electrónico:</strong> <span t-field="doc.transportista_email"/></td>
                            </tr>
                        </table>

                        <!-- 9-12. INFORMACIÓN DEL TRANSPORTE -->
                        <table>
                            <tr>
                                <td style="width:25%;"><strong>9. Núm. de autorización de la SEMARNAT:</strong><br/><span t-field="doc.numero_autorizacion_semarnat"/></td>
                                <td style="width:25%;"><strong>10. Núm. de permiso S.C.T.:</strong><br/><span t-field="doc.numero_permiso_sct"/></td>
                                <td style="width:25%;"><strong>11. Tipo de vehículo:</strong><br/><span t-field="doc.tipo_vehiculo"/></td>
                                <td style="width:25%;"><strong>12. Núm. de placa:</strong><br/><span t-field="doc.numero_placa"/></td>
                            </tr>
                        </table>

                        <!-- 13. RUTA -->
                        <table>
                            <tr>
                                <td class="labelcell">13. Ruta de la empresa generadora hasta su entrega:</td>
                            </tr>
                            <tr>
                                <td style="height: 28px; vertical-align: top;">
                                    <span t-field="doc.ruta_empresa"/>
                                </td>
                            </tr>
                        </table>

                        <!-- 14. DECLARACIÓN DEL TRANSPORTISTA -->
                        <table class="signature-section">
                            <tr>
                                <td>
                                    <div class="signature-container">
                                        <div class="signature-text">
                                            14. Declaración del transportista: Declaro bajo protesta de decir verdad que recibí los residuos peligrosos descritos en el manifiesto para su transporte a la empresa destinataria señalada por el generador.
                                        </div>
                                        <div class="signature-fields">
                                            <div class="cell" style="width:40%;">
                                                <strong>Nombre y firma del responsable:</strong><br/>
                                                <span t-field="doc.transportista_responsable_nombre"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Fecha:</strong><br/>
                                                <span t-field="doc.transportista_fecha"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Sello:</strong><br/>
                                                <span t-field="doc.transportista_sello"/>
                                            </div>
                                        </div>
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <!-- 15. DESTINATARIO -->
                        <table>
                            <tr>
                                <td colspan="4" class="labelcell">15. Nombre o razón social del destinatario: <span t-field="doc.destinatario_nombre"/></td>
                            </tr>
                            <tr>
                                <td class="subcell" style="width:25%;">Código postal: <span t-field="doc.destinatario_codigo_postal"/></td>
                                <td class="subcell" style="width:25%;">Calle: <span t-field="doc.destinatario_calle"/></td>
                                <td class="subcell" style="width:25%;">Núm. Ext.: <span t-field="doc.destinatario_num_ext"/></td>
                                <td class="subcell" style="width:25%;">Núm. Int.: <span t-field="doc.destinatario_num_int"/></td>
                            </tr>
                            <tr>
                                <td><strong>Colonia:</strong> <span t-field="doc.destinatario_colonia"/></td>
                                <td><strong>Municipio o Delegación:</strong> <span t-field="doc.destinatario_municipio"/></td>
                                <td colspan="2"><strong>Estado:</strong> <span t-field="doc.destinatario_estado"/></td>
                            </tr>
                            <tr>
                                <td><strong>Teléfono:</strong> <span t-field="doc.destinatario_telefono"/></td>
                                <td colspan="3"><strong>Correo electrónico:</strong> <span t-field="doc.destinatario_email"/></td>
                            </tr>
                        </table>

                        <!-- 16-18. INFORMACIÓN ADICIONAL DEL DESTINATARIO -->
                        <table>
                            <tr>
                                <td style="width:50%;"><strong>16. Núm. autorización de la SEMARNAT:</strong><br/><span t-field="doc.numero_autorizacion_semarnat_destinatario"/></td>
                                <td style="width:50%;"><strong>17. Nombre y cargo de la persona que recibe los residuos:</strong><br/><span t-field="doc.nombre_persona_recibe"/></td>
                            </tr>
                            <tr>
                                <td colspan="2" class="labelcell">18. Observaciones:</td>
                            </tr>
                            <tr>
                                <td colspan="2" style="height: 28px; vertical-align: top;">
                                    <span t-field="doc.observaciones_destinatario"/>
                                </td>
                            </tr>
                        </table>

                        <!-- 19. DECLARACIÓN DEL DESTINATARIO -->
                        <table class="signature-section">
                            <tr>
                                <td>
                                    <div class="signature-container">
                                        <div class="signature-text">
                                            19. Declaración del destinatario: Declaro bajo protesta de decir verdad que recibí los residuos peligrosos descritos en el manifiesto.
                                        </div>
                                        <div class="signature-fields">
                                            <div class="cell" style="width:40%;">
                                                <strong>Nombre y firma del responsable:</strong><br/>
                                                <span t-field="doc.destinatario_responsable_nombre"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Fecha:</strong><br/>
                                                <span t-field="doc.destinatario_fecha"/>
                                            </div>
                                            <div class="cell" style="width:30%;">
                                                <strong>Sello:</strong><br/>
                                                <span t-field="doc.destinatario_sello"/>
                                            </div>
                                        </div>
                                    </div>
                                </td>
                            </tr>
                        </table>
                    </div>
                </t>
            </t>
        </t>
    </template>
</odoo>```

## ./views/salida_acopio_menus.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <!-- MENÚ RAÍZ INDEPENDIENTE -->
    <menuitem id="menu_salida_acopio_root"
              name="Destino Final"
              sequence="16"
              web_icon="salida_acopio_manifiesto,static/description/icon.png"/>

    <!-- Submenú principal dentro del menú raíz -->
    <menuitem id="menu_salida_acopio_main"
              name="Salidas de Acopio"
              parent="menu_salida_acopio_root"
              action="action_salida_acopio"
              sequence="10"/>

    <!-- Submenú para nueva salida (wizard) -->
    <menuitem id="menu_salida_acopio_nueva"
              name="Nueva Salida"
              parent="menu_salida_acopio_root"
              action="action_salida_acopio_wizard"
              sequence="20"/>

    <!-- Acción para solo salidas realizadas -->
    <record id="action_salida_acopio_realizadas" model="ir.actions.act_window">
        <field name="name">Salidas Realizadas</field>
        <field name="res_model">salida.acopio</field>
        <field name="view_mode">list,form</field>
        <field name="context">{'search_default_done': 1}</field>
        <field name="help" type="html">
            <p class="o_view_nocontent_smiling_face">
                No hay salidas de acopio realizadas
            </p>
            <p>
                Vista filtrada para mostrar solo las salidas de acopio confirmadas.
            </p>
        </field>
    </record>

    <!-- Submenú para salidas realizadas -->
    <menuitem id="menu_salida_acopio_realizadas"
              name="Salidas Realizadas"
              parent="menu_salida_acopio_root"
              action="action_salida_acopio_realizadas"
              sequence="30"/>

    <!-- Acción para ver manifiestos de salida -->
    <record id="action_manifiestos_salida" model="ir.actions.act_window">
        <field name="name">Manifiestos de Salida (SAI como Generador)</field>
        <field name="res_model">manifiesto.ambiental</field>
        <field name="view_mode">list,form</field>
        <field name="domain">[('generador_nombre', 'ilike', 'SAI')]</field>
        <field name="context">{'search_default_confirmed': 1}</field>
        <field name="help" type="html">
            <p class="o_view_nocontent_smiling_face">
                No hay manifiestos de salida generados
            </p>
            <p>
                Manifiestos donde SAI aparece como generador, creados automáticamente
                desde las salidas de acopio.
            </p>
        </field>
    </record>

    <!-- Submenú para manifiestos de salida -->
    <menuitem id="menu_manifiestos_salida"
              name="Manifiestos de Salida"
              parent="menu_salida_acopio_root"
              action="action_manifiestos_salida"
              sequence="40"/>
</odoo>```

## ./views/salida_acopio_print_views.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <record id="view_salida_acopio_form_print_button" model="ir.ui.view">
        <field name="name">salida.acopio.form.print.button</field>
        <field name="model">salida.acopio</field>
        <field name="inherit_id" ref="salida_acopio_manifiesto.view_salida_acopio_form"/>
        <field name="arch" type="xml">
            <xpath expr="//button[@name='action_view_manifiesto'][2]" position="after">
                <button name="action_print_manifiesto_salida"
                        string="Imprimir Manifiesto Salida"
                        type="object"
                        class="btn-primary"
                        invisible="state != 'done'"/>
            </xpath>
        </field>
    </record>
</odoo>```

## ./views/salida_acopio_views.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <record id="action_salida_acopio" model="ir.actions.act_window">
        <field name="name">Salidas de Acopio</field>
        <field name="res_model">salida.acopio</field>
        <field name="view_mode">list,form</field>
        <field name="help" type="html">
            <p class="o_view_nocontent_smiling_face">
                No hay salidas de acopio registradas
            </p>
            <p>
                Las salidas de acopio permiten dar de salida residuos del inventario hacia su disposición final,
                generando automáticamente manifiestos donde SAI es el generador.
            </p>
        </field>
    </record>

    <record id="action_salida_acopio_wizard" model="ir.actions.act_window">
        <field name="name">Nueva Salida de Acopio</field>
        <field name="res_model">salida.acopio.wizard</field>
        <field name="view_mode">form</field>
        <field name="target">new</field>
    </record>

    <record id="view_salida_acopio_list" model="ir.ui.view">
        <field name="name">salida.acopio.list</field>
        <field name="model">salida.acopio</field>
        <field name="arch" type="xml">
            <list string="Salidas de Acopio" default_order="fecha_salida desc">
                <field name="numero_referencia"/>
                <field name="transportista_id"/>
                <field name="destinatario_id"/>
                <field name="fecha_salida"/>
                <field name="usuario_salida"/>
                <field name="total_residuos"/>
                <field name="cantidad_total"/>
                <field name="manifiesto_salida_id"/>
                <field name="state"
                       widget="badge"
                       decoration-success="state == 'done'"
                       decoration-muted="state == 'cancel'"/>
                <button name="action_view_picking"
                        type="object"
                        string="Ver Transferencia"
                        class="btn-link"
                        invisible="state != 'done'"/>
                <button name="action_view_manifiesto"
                        type="object"
                        string="Ver Manifiesto"
                        class="btn-link"
                        invisible="state != 'done'"/>
            </list>
        </field>
    </record>

    <record id="view_salida_acopio_form" model="ir.ui.view">
        <field name="name">salida.acopio.form</field>
        <field name="model">salida.acopio</field>
        <field name="arch" type="xml">
            <form string="Salida de Acopio">
                <header>
                    <button name="action_confirmar_salida"
                            string="Confirmar Salida"
                            type="object"
                            class="btn-primary"
                            invisible="state != 'draft'"
                            confirm="¿Está seguro de confirmar esta salida? Se crearán movimientos de inventario y el manifiesto."/>
                    <button name="action_view_picking"
                            string="Ver Transferencia"
                            type="object"
                            class="btn-secondary"
                            invisible="state != 'done'"/>
                    <button name="action_view_manifiesto"
                            string="Ver Manifiesto"
                            type="object"
                            class="btn-secondary"
                            invisible="state != 'done'"/>
                    <button name="action_cancelar"
                            string="Cancelar"
                            type="object"
                            invisible="state != 'draft'"
                            confirm="¿Está seguro de cancelar esta salida?"/>
                    <field name="state" widget="statusbar" statusbar_visible="draft,done"/>
                </header>

                <sheet>
                    <div class="oe_button_box" name="button_box">
                        <button name="action_view_picking"
                                type="object"
                                class="oe_stat_button"
                                icon="fa-truck"
                                invisible="state != 'done'">
                            <div class="o_field_widget o_stat_info">
                                <span class="o_stat_text">Transferencia</span>
                            </div>
                        </button>
                        <button name="action_view_manifiesto"
                                type="object"
                                class="oe_stat_button"
                                icon="fa-file-text-o"
                                invisible="state != 'done'">
                            <div class="o_field_widget o_stat_info">
                                <span class="o_stat_text">Manifiesto Generado</span>
                            </div>
                        </button>
                    </div>

                    <div class="oe_title">
                        <h1><field name="numero_referencia" readonly="1"/></h1>
                    </div>

                    <div class="alert alert-success"
                         style="margin-bottom: 20px;"
                         invisible="state != 'done'">
                        <strong>✅ Manifiesto Generado:</strong>
                        <field name="manifiesto_salida_id" readonly="1"/>
                        (SAI como generador)
                    </div>

                    <group string="Información del Transporte" col="4">
                        <field name="transportista_id" readonly="state != 'draft'" options="{'no_create': True}"/>
                        <field name="destinatario_id" readonly="state != 'draft'" options="{'no_create': True}"/>
                        <field name="fecha_salida" readonly="state != 'draft'"/>
                        <field name="usuario_salida" readonly="1"/>
                    </group>

                    <group string="Resumen" col="4">
                        <field name="total_residuos" readonly="1"/>
                        <field name="cantidad_total" readonly="1"/>
                        <field name="company_id" groups="base.group_multi_company" readonly="state != 'draft'"/>
                    </group>

                    <notebook>
                        <page string="Líneas de Salida">
                            <field name="linea_ids" readonly="state != 'draft'">
                                <list editable="bottom">
                                    <field name="producto_id" options="{'no_create': True}"/>
                                    <!-- ✅ lote_domain_ids invisible expone los IDs al cliente para el domain -->
                                    <field name="lote_domain_ids" column_invisible="1"/>
                                    <field name="lote_id"
                                           domain="[('id', 'in', lote_domain_ids)]"
                                           options="{'no_create': True}"/>
                                    <field name="stock_disponible" readonly="1"/>
                                    <field name="cantidad"/>
                                    <field name="clasificaciones_cretib" readonly="1"/>
                                </list>
                                <form string="Línea de Salida">
                                    <group col="2">
                                        <field name="producto_id" options="{'no_create': True}"/>
                                        <field name="lote_domain_ids" invisible="1"/>
                                        <field name="lote_id"
                                               domain="[('id', 'in', lote_domain_ids)]"
                                               options="{'no_create': True}"/>
                                        <field name="stock_disponible" readonly="1"/>
                                        <field name="cantidad"/>
                                        <field name="clasificaciones_cretib" readonly="1"/>
                                    </group>
                                </form>
                            </field>
                        </page>

                        <page string="Observaciones">
                            <group>
                                <field name="observaciones" nolabel="1"
                                       placeholder="Observaciones adicionales..."
                                       readonly="state != 'draft'"/>
                            </group>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <record id="view_salida_acopio_search" model="ir.ui.view">
        <field name="name">salida.acopio.search</field>
        <field name="model">salida.acopio</field>
        <field name="arch" type="xml">
            <search string="Buscar Salidas de Acopio">
                <field name="numero_referencia"/>
                <field name="transportista_id"/>
                <field name="destinatario_id"/>
                <field name="manifiesto_salida_id"/>
                <field name="usuario_salida"/>
                <filter string="Borradores" name="draft" domain="[('state','=','draft')]"/>
                <filter string="Realizadas" name="done" domain="[('state','=','done')]"/>
                <filter string="Canceladas" name="cancel" domain="[('state','=','cancel')]"/>
                <filter string="Hoy" name="today"
                        domain="[('fecha_salida', '&gt;=', context_today().strftime('%Y-%m-%d'))]"/>
                <filter string="Esta Semana" name="this_week"
                        domain="[('fecha_salida', '&gt;=', (context_today() - datetime.timedelta(days=7)).strftime('%Y-%m-%d'))]"/>
                <filter string="Este Mes" name="this_month"
                        domain="[('fecha_salida', '&gt;=', context_today().strftime('%Y-%m-01'))]"/>
                    <filter string="Estado" name="group_state" context="{'group_by':'state'}"/>
                    <filter string="Transportista" name="group_transportista" context="{'group_by':'transportista_id'}"/>
                    <filter string="Destinatario" name="group_destinatario" context="{'group_by':'destinatario_id'}"/>
                    <filter string="Usuario" name="group_user" context="{'group_by':'usuario_salida'}"/>
                    <filter string="Fecha" name="group_date" context="{'group_by':'fecha_salida:day'}"/>
            </search>
        </field>
    </record>
</odoo>```

## ./wizard/__init__.py
```py
# -*- coding: utf-8 -*-
from . import salida_acopio_wizard```

## ./wizard/salida_acopio_wizard_views.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <!-- Vista del wizard para salida de acopio -->
    <record id="view_salida_acopio_wizard_form" model="ir.ui.view">
        <field name="name">salida.acopio.wizard.form</field>
        <field name="model">salida.acopio.wizard</field>
        <field name="arch" type="xml">
            <form string="Salida de Acopio">
                <div class="alert alert-warning" style="margin-bottom: 20px;">
                    <h4><strong>📤 Salida de Acopio del Inventario</strong></h4>
                    <p>
                        Se van a dar de salida residuos desde la ubicación "Acopio" del inventario 
                        y se generará automáticamente un manifiesto ambiental donde <strong>SAI es el generador</strong>.
                    </p>
                </div>

                <group string="Información del Transporte" col="2">
                    <field name="transportista_id" 
                           placeholder="Transportista (SAI por defecto)..."
                           options="{'no_create': True}"/>
                    <field name="destinatario_id" 
                           placeholder="Seleccione el destinatario final..."
                           options="{'no_create': True}"/>
                </group>

                <group string="Resumen" col="2">
                    <field name="total_residuos" readonly="1"/>
                    <field name="cantidad_total" readonly="1"/>
                </group>

                <group string="Residuos a Dar de Salida">
                    <field name="linea_ids" nolabel="1">
                        <list editable="bottom" string="Residuos">
                            <field name="producto_id" 
                                   placeholder="Seleccione producto con stock en Acopio..."
                                   options="{'no_create': True}"/>
                            <field name="lote_id" 
                                   domain="[('id', 'in', lotes_disponibles_ids)]"
                                   options="{'no_create': True}"/>
                            <field name="stock_disponible" readonly="1"/>
                            <field name="cantidad" 
                                   decoration-bf="cantidad > stock_disponible"
                                   decoration-danger="cantidad > stock_disponible"/>
                            <field name="clasificaciones_cretib" readonly="1" optional="show"/>
                            <field name="lotes_disponibles_ids" column_invisible="1"/>
                        </list>
                        <form string="Línea de Residuo">
                            <group col="2">
                                <field name="producto_id" 
                                       placeholder="Seleccione producto con stock en Acopio..."
                                       options="{'no_create': True}"/>
                                <field name="lote_id" 
                                       domain="[('id', 'in', lotes_disponibles_ids)]"
                                       options="{'no_create': True}"/>
                                <field name="stock_disponible" readonly="1"/>
                                <field name="cantidad"/>
                                <field name="clasificaciones_cretib" readonly="1"/>
                                <field name="lotes_disponibles_ids" invisible="1"/>
                            </group>
                            
                            <div class="alert alert-info" invisible="not lotes_disponibles_ids">
                                <strong>Lotes disponibles para este producto:</strong>
                                <field name="lotes_disponibles_ids" widget="many2many_tags" readonly="1"/>
                            </div>
                        </form>
                    </field>
                </group>

                <group string="Observaciones">
                    <field name="observaciones" 
                           nolabel="1" 
                           placeholder="Observaciones adicionales para la salida y el manifiesto..."/>
                </group>

                <div class="alert alert-info" style="margin-top: 15px;">
                    <strong>ℹ️ Información importante:</strong>
                    <ul style="margin: 5px 0;">
                        <li>Solo se muestran productos que tienen stock en la ubicación "Acopio"</li>
                        <li>Para productos con seguimiento por lotes, debe seleccionar un lote específico</li>
                        <li>Se verificará que haya stock suficiente antes de confirmar</li>
                        <li>Se generará automáticamente un manifiesto donde SAI aparece como generador</li>
                        <li>Esta operación creará movimientos de inventario de salida</li>
                    </ul>
                </div>

                <footer>
                    <button string="✅ Confirmar Salida" 
                            name="action_confirmar_salida" 
                            type="object" 
                            class="btn-primary"
                            confirm="¿Está seguro de que desea realizar la salida de acopio? Se crearán movimientos de inventario y un manifiesto ambiental."/>
                    <button string="Cancelar" 
                            class="btn-secondary" 
                            special="cancel"/>
                </footer>
            </form>
        </field>
    </record>
</odoo>```

## ./wizard/salida_acopio_wizard.py
```py
# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class SalidaAcopioWizard(models.TransientModel):
    _name = 'salida.acopio.wizard'
    _description = 'Wizard para Salida de Acopio'

    transportista_id = fields.Many2one(
        'res.partner',
        string='Transportista',
        domain=[('is_company', '=', True)],
        default=lambda self: self._get_sai_partner(),
        required=True,
        help='Empresa transportista (SAI por defecto)'
    )

    destinatario_id = fields.Many2one(
        'res.partner',
        string='Destinatario Final',
        domain=[('is_company', '=', True)],
        required=True,
        help='Empresa destinataria final de los residuos'
    )

    linea_ids = fields.One2many(
        'salida.acopio.wizard.linea',
        'wizard_id',
        string='Residuos a Dar de Salida'
    )

    total_residuos = fields.Integer(
        string='Total de Residuos',
        compute='_compute_totales'
    )

    cantidad_total = fields.Float(
        string='Cantidad Total (kg)',
        compute='_compute_totales'
    )

    observaciones = fields.Text(
        string='Observaciones'
    )

    def _get_sai_partner(self):
        sai_partner = self.env['res.partner'].search([
            ('is_company', '=', True),
            ('name', 'ilike', 'SAI')
        ], limit=1)
        if sai_partner:
            return sai_partner.id
        try:
            transportista = self.env['res.partner'].search([
                ('es_transportista', '=', True)
            ], limit=1)
            if transportista:
                return transportista.id
        except Exception:
            pass
        empresa = self.env['res.partner'].search([
            ('is_company', '=', True)
        ], limit=1)
        return empresa.id if empresa else False

    @api.depends('linea_ids.cantidad')
    def _compute_totales(self):
        for record in self:
            record.total_residuos = len(record.linea_ids)
            record.cantidad_total = sum(record.linea_ids.mapped('cantidad'))

    def action_confirmar_salida(self):
        self.ensure_one()
        if not self.linea_ids:
            raise UserError("No hay residuos para dar de salida.")
        if not self.transportista_id:
            raise UserError("Debe seleccionar un transportista.")
        if not self.destinatario_id:
            raise UserError("Debe seleccionar un destinatario final.")

        lineas_data = []
        for linea in self.linea_ids:
            _logger.info(f"Validando línea: ID={linea.id}, producto_id={linea.producto_id.id if linea.producto_id else 'None'}")
            if not linea.producto_id or not linea.producto_id.id:
                raise UserError(f"Una de las líneas no tiene producto asignado. Línea ID: {linea.id}")
            if linea.cantidad <= 0:
                raise UserError(f"La cantidad para el producto {linea.producto_id.name} debe ser mayor a cero.")
            if linea.cantidad > linea.stock_disponible:
                raise UserError(
                    f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                    f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg"
                )
            lineas_data.append({
                'producto_id': linea.producto_id.id,
                'lote_id': linea.lote_id.id if linea.lote_id else False,
                'cantidad': linea.cantidad,
            })

        _logger.info(f"Validadas {len(lineas_data)} líneas correctamente")

        try:
            salida_vals = {
                'transportista_id': self.transportista_id.id,
                'destinatario_id': self.destinatario_id.id,
                'observaciones': self.observaciones,
            }
            salida = self.env['salida.acopio'].create(salida_vals)
            _logger.info(f"Creada salida de acopio: {salida.numero_referencia}")

            for linea_data in lineas_data:
                self.env['salida.acopio.linea'].create({
                    'salida_id': salida.id,
                    'producto_id': linea_data['producto_id'],
                    'lote_id': linea_data['lote_id'],
                    'cantidad': linea_data['cantidad'],
                })

            salida.action_confirmar_salida()

            return {
                'name': 'Salida de Acopio Realizada',
                'type': 'ir.actions.act_window',
                'res_model': 'salida.acopio',
                'view_mode': 'form',
                'res_id': salida.id,
                'target': 'current',
            }
        except Exception as e:
            _logger.error(f"Error al confirmar salida de acopio: {str(e)}")
            raise UserError(f"Error al procesar la salida: {str(e)}")


class SalidaAcopioWizardLinea(models.TransientModel):
    _name = 'salida.acopio.wizard.linea'
    _description = 'Línea del Wizard de Salida de Acopio'

    wizard_id = fields.Many2one(
        'salida.acopio.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade'
    )

    producto_id = fields.Many2one(
        'product.product',
        string='Producto/Residuo',
        required=True,
        help='Producto disponible en la ubicación Acopio'
    )

    lote_id = fields.Many2one(
        'stock.lot',
        string='Lote',
        help='Lote específico del producto'
    )

    cantidad = fields.Float(
        string='Cantidad a Salir (kg)',
        required=True,
        digits=(12, 3),
        default=0.0
    )

    stock_disponible = fields.Float(
        string='Stock Disponible (kg)',
        compute='_compute_stock_disponible',
        help='Cantidad disponible en la ubicación Acopio'
    )

    clasificaciones_cretib = fields.Char(
        string='CRETIB',
        compute='_compute_clasificaciones_cretib',
        readonly=True
    )

    # ✅ CLAVE: store=True para que el domain funcione en la vista
    lotes_disponibles_ids = fields.Many2many(
        'stock.lot',
        'salida_acopio_wiz_linea_lot_rel',
        'linea_id',
        'lot_id',
        string='Lotes Disponibles',
        store=True,
        help='Lotes con stock positivo para este producto en Acopio'
    )

    def _get_location_acopio(self):
        company = self.env.company
        _logger.info(f"[ACOPIO DEBUG] Buscando ubicación Acopio para company_id={company.id} ({company.name})")
        location = self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', company.id)
        ], limit=1)
        if location:
            _logger.info(f"[ACOPIO DEBUG] Ubicación encontrada: id={location.id}, complete_name={location.complete_name}")
        else:
            # Intentar sin filtro de compañía para ver qué existe
            todas = self.env['stock.location'].search([('name', '=', 'Acopio')])
            _logger.warning(f"[ACOPIO DEBUG] NO se encontró Acopio para company_id={company.id}. "
                            f"Ubicaciones 'Acopio' existentes: {[(l.id, l.complete_name, l.company_id.id) for l in todas]}")
        return location

    def _recompute_lotes_disponibles(self):
        """Recomputa y guarda lotes disponibles para el producto actual."""
        _logger.info(f"[ACOPIO DEBUG] _recompute_lotes_disponibles() llamado. producto_id={self.producto_id.id if self.producto_id else None}")
        location_acopio = self._get_location_acopio()
        if self.producto_id and location_acopio:
            # Sin filtro de quantity para ver todo lo que hay
            todos_quants = self.env['stock.quant'].search([
                ('product_id', '=', self.producto_id.id),
                ('location_id', '=', location_acopio.id),
            ])
            _logger.info(f"[ACOPIO DEBUG] Todos los quants en Acopio para producto {self.producto_id.name} "
                         f"(id={self.producto_id.id}): "
                         f"{[(q.id, q.lot_id.name if q.lot_id else 'sin lote', q.quantity, q.reserved_quantity) for q in todos_quants]}")

            quants = todos_quants.filtered(lambda q: q.quantity > 0 and q.lot_id)
            lot_ids = quants.mapped('lot_id').ids
            _logger.info(f"[ACOPIO DEBUG] Quants con quantity>0 y lote: {[(q.lot_id.name, q.quantity) for q in quants]}")
            _logger.info(f"[ACOPIO DEBUG] lot_ids resultantes: {lot_ids}")
            self.lotes_disponibles_ids = [(6, 0, lot_ids)]
            _logger.info(f"[ACOPIO DEBUG] lotes_disponibles_ids asignados: {self.lotes_disponibles_ids.ids}")
        else:
            if not self.producto_id:
                _logger.warning("[ACOPIO DEBUG] No hay producto_id seleccionado")
            if not location_acopio:
                _logger.warning("[ACOPIO DEBUG] No se encontró la ubicación Acopio")
            self.lotes_disponibles_ids = [(5, 0, 0)]

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

    @api.depends('producto_id')
    def _compute_clasificaciones_cretib(self):
        for record in self:
            if record.producto_id and hasattr(record.producto_id, 'get_clasificaciones_cretib'):
                record.clasificaciones_cretib = record.producto_id.get_clasificaciones_cretib()
            else:
                record.clasificaciones_cretib = ''

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        self.lote_id = False
        self.cantidad = 0.0
        # ✅ Recomputar y guardar los lotes disponibles para que el domain funcione
        self._recompute_lotes_disponibles()
        return {
            'domain': {
                'lote_id': [('id', 'in', self.lotes_disponibles_ids.ids)]
            }
        }

    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        if self.lote_id and self.producto_id and self.stock_disponible > 0:
            self.cantidad = self.stock_disponible

    @api.onchange('cantidad')
    def _onchange_cantidad(self):
        if self.cantidad and self.stock_disponible and self.cantidad > self.stock_disponible:
            return {
                'warning': {
                    'title': 'Stock Insuficiente',
                    'message': (
                        f'La cantidad solicitada ({self.cantidad} kg) '
                        f'excede el stock disponible ({self.stock_disponible} kg)'
                    )
                }
            }

    @api.constrains('cantidad', 'stock_disponible')
    def _check_cantidad_disponible(self):
        for record in self:
            if record.cantidad > 0 and record.cantidad > record.stock_disponible:
                raise ValidationError(
                    f"La cantidad a dar de salida ({record.cantidad} kg) no puede ser mayor "
                    f"al stock disponible ({record.stock_disponible} kg) para el producto "
                    f"{record.producto_id.name}"
                )

    @api.constrains('cantidad')
    def _check_cantidad_positiva(self):
        for record in self:
            if record.cantidad <= 0:
                raise ValidationError("La cantidad debe ser mayor a cero.")```

