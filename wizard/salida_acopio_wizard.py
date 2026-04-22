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
            if not linea.producto_id or not linea.producto_id.id:
                raise UserError(
                    f"Una de las líneas no tiene producto asignado. Línea ID: {linea.id}"
                )
            if linea.cantidad <= 0:
                raise UserError(
                    f"La cantidad para el producto {linea.producto_id.name} debe ser mayor a cero."
                )
            if linea.cantidad > linea.stock_disponible:
                raise UserError(
                    f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                    f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg"
                )
            lineas_data.append({
                'producto_id': linea.producto_id.id,
                'lote_id': linea.lote_id.id if linea.lote_id else False,
                'cantidad': linea.cantidad,
                'nombre_residuo': linea.nombre_residuo or '',
                'residue_type': linea.residue_type or False,
                'clasificacion_corrosivo': linea.clasificacion_corrosivo,
                'clasificacion_reactivo': linea.clasificacion_reactivo,
                'clasificacion_explosivo': linea.clasificacion_explosivo,
                'clasificacion_toxico': linea.clasificacion_toxico,
                'clasificacion_inflamable': linea.clasificacion_inflamable,
                'clasificacion_biologico': linea.clasificacion_biologico,
                'envase_tipo': linea.envase_tipo or False,
                'packaging_id': linea.packaging_id.id if linea.packaging_id else False,
                'envase_cantidad': linea.envase_cantidad or 1,
                'envase_capacidad': linea.envase_capacidad or '',
                'tipo_manejo_id': linea.tipo_manejo_id.id if linea.tipo_manejo_id else False,
                'etiqueta_si': linea.etiqueta_si,
                'etiqueta_no': linea.etiqueta_no,
            })

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
                    **linea_data,
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
        string='CRETIB',
        compute='_compute_clasificaciones_cretib',
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
        _logger.info(f"[ACOPIO WIZARD] Lotes disponibles para {self.producto_id.name}: {lot_ids}")
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
            return {'domain': {'lote_id': [('id', '=', False)]}}
        self._load_from_product()
        lot_ids = self._get_lot_ids_in_acopio()
        return {'domain': {'lote_id': [('id', 'in', lot_ids)]}}

    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        if not self.lote_id or not self.producto_id:
            if not self.lote_id:
                self.cantidad = 0.0
            return
        location_acopio = self._get_location_acopio()
        if location_acopio:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', self.producto_id.id),
                ('location_id', '=', location_acopio.id),
                ('lot_id', '=', self.lote_id.id),
                ('quantity', '>', 0),
            ])
            disponible = sum(quants.mapped('quantity'))
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
                raise ValidationError("La cantidad debe ser mayor a cero.")