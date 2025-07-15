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
            record.es_salida_acopio = bool(record.salida_acopio_id)