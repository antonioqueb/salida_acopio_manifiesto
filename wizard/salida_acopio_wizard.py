# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class SalidaAcopioWizard(models.TransientModel):
    _name = 'salida.acopio.wizard'
    _description = 'Wizard para Salida de Acopio'

    # CORREGIDO: Domain más flexible para transportista
    transportista_id = fields.Many2one(
        'res.partner',
        string='Transportista',
        domain=[('is_company', '=', True)],  # CAMBIADO: Domain más flexible
        default=lambda self: self._get_sai_partner(),
        required=True,
        help='Empresa transportista (SAI por defecto)'
    )
    
    # CORREGIDO: Domain más flexible para destinatario
    destinatario_id = fields.Many2one(
        'res.partner',
        string='Destinatario Final',
        domain=[('is_company', '=', True)],  # Solo empresas, más flexible
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
        """Buscar el partner SAI o retornar transportista disponible"""
        # ACTUALIZADO: Buscar con domain más flexible
        # Primero buscar un partner que tenga 'SAI' en el nombre y sea empresa
        sai_partner = self.env['res.partner'].search([
            ('is_company', '=', True),
            ('name', 'ilike', 'SAI')
        ], limit=1)
        
        if sai_partner:
            return sai_partner.id
        
        # Si no encontramos partner SAI, buscar si existe el campo es_transportista
        try:
            transportista = self.env['res.partner'].search([
                ('es_transportista', '=', True)
            ], limit=1)
            
            if transportista:
                return transportista.id
        except:
            # El campo es_transportista no existe, continuar con empresas
            pass
        
        # Si no hay transportistas específicos, buscar cualquier empresa
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
        """
        Confirma la salida y crea el registro de salida de acopio
        """
        self.ensure_one()
        
        # Validaciones iniciales
        if not self.linea_ids:
            raise UserError("No hay residuos para dar de salida.")
        
        if not self.transportista_id:
            raise UserError("Debe seleccionar un transportista.")
            
        if not self.destinatario_id:
            raise UserError("Debe seleccionar un destinatario final.")
        
        # Validar cada línea individualmente
        lineas_data = []
        for linea in self.linea_ids:
            _logger.info(f"Validando línea: ID={linea.id}, producto_id={linea.producto_id.id if linea.producto_id else 'None'}")
            
            # Verificar que el producto_id existe
            if not linea.producto_id or not linea.producto_id.id:
                raise UserError(f"Una de las líneas no tiene producto asignado. Línea ID: {linea.id}")
            
            # Verificar que la cantidad es válida
            if linea.cantidad <= 0:
                raise UserError(f"La cantidad para el producto {linea.producto_id.name} debe ser mayor a cero.")
            
            # Verificar stock disponible
            if linea.cantidad > linea.stock_disponible:
                raise UserError(f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                               f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg")
            
            # Guardar datos de la línea
            lineas_data.append({
                'producto_id': linea.producto_id.id,
                'lote_id': linea.lote_id.id if linea.lote_id else False,
                'cantidad': linea.cantidad,
            })
        
        _logger.info(f"Validadas {len(lineas_data)} líneas correctamente")
        
        try:
            # Crear registro de salida de acopio
            salida_vals = {
                'transportista_id': self.transportista_id.id,
                'destinatario_id': self.destinatario_id.id,
                'observaciones': self.observaciones,
            }
            
            salida = self.env['salida.acopio'].create(salida_vals)
            _logger.info(f"Creada salida de acopio: {salida.numero_referencia}")
            
            # Crear líneas de salida usando los datos guardados
            for linea_data in lineas_data:
                linea_vals = {
                    'salida_id': salida.id,
                    'producto_id': linea_data['producto_id'],
                    'lote_id': linea_data['lote_id'],
                    'cantidad': linea_data['cantidad'],
                }
                self.env['salida.acopio.linea'].create(linea_vals)
            
            # Confirmar la salida automáticamente
            salida.action_confirmar_salida()
            
            # Mostrar mensaje de éxito y abrir el registro creado
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
    
    # CORREGIDO: Mostrar solo productos con stock en Acopio
    producto_id = fields.Many2one(
        'product.product',
        string='Producto/Residuo',
        required=True,
        help='Producto disponible en la ubicación Acopio'
    )
    
    lote_id = fields.Many2one(
        'stock.lot',
        string='Lote',
        help='Lote específico del producto (requerido si el producto tiene seguimiento por lotes)'
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
    
    # CAMPO CLAVE: Para el domain dinámico de lotes
    lotes_disponibles_ids = fields.Many2many(
        'stock.lot',
        compute='_compute_lotes_disponibles',
        string='Lotes Disponibles',
        help='Lotes con stock positivo para este producto en Acopio'
    )

    @api.depends('producto_id')
    def _compute_lotes_disponibles(self):
        """Calcular lotes que tengan stock en Acopio para el producto seleccionado"""
        for record in self:
            if record.producto_id:
                # Buscar ubicación Acopio
                location_acopio = self.env['stock.location'].search([
                    ('name', '=', 'Acopio'),
                    ('company_id', '=', self.env.company.id)
                ], limit=1)
                
                if location_acopio:
                    # Buscar quants con stock positivo en Acopio para este producto
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', record.producto_id.id),
                        ('location_id', '=', location_acopio.id),
                        ('quantity', '>', 0),
                        ('lot_id', '!=', False)
                    ])
                    lotes_ids = quants.mapped('lot_id').ids
                    record.lotes_disponibles_ids = [(6, 0, lotes_ids)]
                else:
                    record.lotes_disponibles_ids = [(6, 0, [])]
            else:
                record.lotes_disponibles_ids = [(6, 0, [])]

    @api.depends('producto_id', 'lote_id')
    def _compute_stock_disponible(self):
        """Calcular stock disponible en ubicación Acopio"""
        for record in self:
            if record.producto_id:
                # Buscar stock en ubicación Acopio
                location_acopio = self.env['stock.location'].search([
                    ('name', '=', 'Acopio'),
                    ('company_id', '=', self.env.company.id)
                ], limit=1)
                
                if location_acopio:
                    domain = [
                        ('product_id', '=', record.producto_id.id),
                        ('location_id', '=', location_acopio.id),
                        ('quantity', '>', 0)
                    ]
                    
                    # Si hay lote seleccionado, filtrar por ese lote específico
                    if record.lote_id:
                        domain.append(('lot_id', '=', record.lote_id.id))
                    
                    quants = self.env['stock.quant'].search(domain)
                    record.stock_disponible = sum(quants.mapped('quantity'))
                else:
                    record.stock_disponible = 0.0
            else:
                record.stock_disponible = 0.0

    @api.depends('producto_id')
    def _compute_clasificaciones_cretib(self):
        """Mostrar clasificaciones CRETIB del producto"""
        for record in self:
            if record.producto_id and hasattr(record.producto_id, 'get_clasificaciones_cretib'):
                record.clasificaciones_cretib = record.producto_id.get_clasificaciones_cretib()
            else:
                record.clasificaciones_cretib = ''

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        """Limpiar campos dependientes al cambiar producto"""
        if self.producto_id:
            self.lote_id = False
            self.cantidad = 0.0
            
            # Actualizar domain para lotes
            return {
                'domain': {
                    'lote_id': [('id', 'in', self.lotes_disponibles_ids.ids)]
                }
            }
        else:
            self.lote_id = False
            self.cantidad = 0.0
            
    @api.onchange('lote_id')
    def _onchange_lote_id(self):
        """Sugerir cantidad al seleccionar lote"""
        if self.lote_id and self.producto_id:
            # Sugerir toda la cantidad disponible del lote
            if self.stock_disponible > 0:
                self.cantidad = self.stock_disponible

    @api.onchange('cantidad')
    def _onchange_cantidad(self):
        """Validar cantidad contra stock disponible"""
        if self.cantidad and self.stock_disponible:
            if self.cantidad > self.stock_disponible:
                return {
                    'warning': {
                        'title': 'Stock Insuficiente',
                        'message': f'La cantidad solicitada ({self.cantidad} kg) '
                                 f'excede el stock disponible ({self.stock_disponible} kg)'
                    }
                }

    @api.constrains('cantidad', 'stock_disponible')
    def _check_cantidad_disponible(self):
        """Validar que no se exceda el stock disponible al guardar"""
        for record in self:
            if record.cantidad > 0 and record.cantidad > record.stock_disponible:
                raise ValidationError(
                    f"La cantidad a dar de salida ({record.cantidad} kg) no puede ser mayor "
                    f"al stock disponible ({record.stock_disponible} kg) para el producto "
                    f"{record.producto_id.name}"
                )

    @api.constrains('cantidad')
    def _check_cantidad_positiva(self):
        """Validar que la cantidad sea positiva"""
        for record in self:
            if record.cantidad <= 0:
                raise ValidationError("La cantidad debe ser mayor a cero.")

    # FUNCIÓN HELPER: Domain dinámico para productos con stock
    @api.model
    def _get_productos_con_stock_domain(self):
        """Devolver domain para productos que tengan stock en Acopio"""
        location_acopio = self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', self.env.company.id)
        ], limit=1)
        
        if location_acopio:
            # Buscar productos que tengan stock positivo en Acopio
            quants = self.env['stock.quant'].search([
                ('location_id', '=', location_acopio.id),
                ('quantity', '>', 0)
            ])
            product_ids = quants.mapped('product_id').ids
            return [('id', 'in', product_ids)]
        else:
            return [('id', '=', False)]  # No mostrar productos si no hay ubicación Acopio