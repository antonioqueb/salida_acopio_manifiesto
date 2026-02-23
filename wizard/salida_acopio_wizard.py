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
        return self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', self.env.company.id)
        ], limit=1)

    def _recompute_lotes_disponibles(self):
        """Recomputa y guarda lotes disponibles para el producto actual."""
        location_acopio = self._get_location_acopio()
        if self.producto_id and location_acopio:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', self.producto_id.id),
                ('location_id', '=', location_acopio.id),
                ('quantity', '>', 0),
                ('lot_id', '!=', False),
            ])
            lot_ids = quants.mapped('lot_id').ids
            _logger.info(f"Lotes disponibles para {self.producto_id.name}: {lot_ids}")
            self.lotes_disponibles_ids = [(6, 0, lot_ids)]
        else:
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
                raise ValidationError("La cantidad debe ser mayor a cero.")