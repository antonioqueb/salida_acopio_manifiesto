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
        ).report_action(self.manifiesto_salida_id)