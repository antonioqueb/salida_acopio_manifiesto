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

    manifiesto_salida_id = fields.Many2one(
        'manifiesto.ambiental',
        string='Manifiesto de Salida',
        related='salida_acopio_id.manifiesto_salida_id',
        store=True,
        readonly=True,
        help='Manifiesto ambiental generado en la salida de acopio (SAI como generador)'
    )

    def _compute_es_salida_acopio(self):
        for record in self:
            record.es_salida_acopio = bool(record.salida_acopio_id)


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    # Related al manifiesto de salida del picking — lo usa sai_stock_reports
    # para resolver correctamente el manifiesto en movimientos de egreso.
    manifiesto_salida_override_id = fields.Many2one(
        'manifiesto.ambiental',
        string='Manifiesto de Salida (Override)',
        related='picking_id.manifiesto_salida_id',
        store=True,
        readonly=True,
    )