# -*- coding: utf-8 -*-
from odoo import models
import logging

_logger = logging.getLogger(__name__)


class ManifiestoAmbiental(models.Model):
    _inherit = 'manifiesto.ambiental'

    def write(self, vals):
        res = super().write(vals)
        # Sincronización manifiesto -> salida de acopio.
        # Si se edita manualmente el número del manifiesto de salida, el folio
        # de la salida de acopio que lo generó debe quedar siempre con ese número.
        if 'numero_manifiesto' in vals and not self.env.context.get('skip_salida_acopio_sync'):
            for manifiesto in self:
                nuevo_numero = manifiesto.numero_manifiesto
                if not nuevo_numero:
                    continue
                salidas = self.env['salida.acopio'].search([
                    ('manifiesto_salida_id', '=', manifiesto.id),
                ])
                for salida in salidas:
                    if salida.numero_referencia != nuevo_numero:
                        salida.with_context(skip_manifiesto_sync=True).write({
                            'numero_referencia': nuevo_numero,
                        })
                        _logger.info(
                            "Folio de salida %s sincronizado con manifiesto %s",
                            salida.id, nuevo_numero,
                        )
        return res
