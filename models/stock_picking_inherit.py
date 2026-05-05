# -*- coding: utf-8 -*-
from odoo import models, fields, api


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

    nombre_operador = fields.Char(
        string='Nombre del Operador',
        help='Operador / chofer del vehículo'
    )
    camion = fields.Char(
        string='Camión',
        help='Descripción / tipo de vehículo'
    )
    placa = fields.Char(
        string='Placa',
        help='Número de placa del vehículo'
    )

    def _compute_es_salida_acopio(self):
        for record in self:
            record.es_salida_acopio = bool(record.salida_acopio_id)


class StockMove(models.Model):
    _inherit = 'stock.move'

    salida_acopio_linea_id = fields.Many2one(
        'salida.acopio.linea',
        string='Línea Salida Acopio',
        ondelete='set null',
    )

    clasificacion_corrosivo = fields.Boolean(string='Corrosivo (C)')
    clasificacion_reactivo = fields.Boolean(string='Reactivo (R)')
    clasificacion_explosivo = fields.Boolean(string='Explosivo (E)')
    clasificacion_toxico = fields.Boolean(string='Tóxico (T)')
    clasificacion_inflamable = fields.Boolean(string='Inflamable (I)')
    clasificacion_biologico = fields.Boolean(string='Biológico (B)')

    cretib_summary = fields.Char(
        string='CRETIB',
        compute='_compute_cretib_summary',
        store=True,
        help='Resumen de las clasificaciones CRETIB del residuo'
    )

    tipo_manejo_salida_id = fields.Many2one(
        'residuo.tipo.manejo',
        string='Plan de Manejo',
        related='salida_acopio_linea_id.tipo_manejo_id',
        store=True,
    )

    nombre_operador = fields.Char(
        string='Nombre del Operador',
        help='Operador / chofer del vehículo'
    )
    camion = fields.Char(
        string='Camión',
        help='Descripción / tipo de vehículo'
    )
    placa = fields.Char(
        string='Placa',
        help='Número de placa del vehículo'
    )

    @api.depends(
        'clasificacion_corrosivo', 'clasificacion_reactivo', 'clasificacion_explosivo',
        'clasificacion_toxico', 'clasificacion_inflamable', 'clasificacion_biologico'
    )
    def _compute_cretib_summary(self):
        for move in self:
            tags = []
            if move.clasificacion_corrosivo:
                tags.append('C')
            if move.clasificacion_reactivo:
                tags.append('R')
            if move.clasificacion_explosivo:
                tags.append('E')
            if move.clasificacion_toxico:
                tags.append('T')
            if move.clasificacion_inflamable:
                tags.append('I')
            if move.clasificacion_biologico:
                tags.append('B')
            move.cretib_summary = ', '.join(tags)