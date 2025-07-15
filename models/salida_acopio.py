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
        default=lambda self: self.env['ir.sequence'].next_by_code('salida.acopio') or 'New'
    )
    
    # Datos del manifiesto generado
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
    
    # Información del transportista y destinatario
    transportista_id = fields.Many2one(
        'res.partner',
        string='Transportista',
        domain=[('is_company', '=', True)],  # CAMBIADO: Domain más flexible
        required=True,
        help='Empresa transportista que llevará los residuos (SAI por defecto)'
    )
    
    destinatario_id = fields.Many2one(
        'res.partner',
        string='Destinatario Final',
        domain=[('is_company', '=', True)],  # CAMBIADO: Domain más flexible
        required=True,
        help='Empresa destinataria final de los residuos'
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
        help='Transferencia de inventario generada para esta salida'
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
    
    observaciones = fields.Text(
        string='Observaciones'
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company
    )

    @api.depends('linea_ids.cantidad')
    def _compute_totales(self):
        for record in self:
            record.total_residuos = len(record.linea_ids)
            record.cantidad_total = sum(record.linea_ids.mapped('cantidad'))

    def name_get(self):
        """Personalizar el nombre mostrado"""
        result = []
        for record in self:
            name = f"{record.numero_referencia}"
            if record.manifiesto_salida_id:
                name += f" - Manifiesto: {record.manifiesto_salida_id.numero_manifiesto}"
            result.append((record.id, name))
        return result

    def action_confirmar_salida(self):
        """
        Confirma la salida, crea los movimientos de inventario y genera el manifiesto
        """
        self.ensure_one()
        
        if self.state != 'draft':
            raise UserError("Solo se pueden confirmar salidas en estado borrador.")
        
        if not self.linea_ids:
            raise UserError("No hay líneas de salida para procesar.")
        
        if not self.transportista_id:
            raise UserError("Debe seleccionar un transportista.")
            
        if not self.destinatario_id:
            raise UserError("Debe seleccionar un destinatario final.")
        
        # NUEVA VALIDACIÓN: Verificar stock disponible antes de procesar
        for linea in self.linea_ids:
            if linea.cantidad > linea.stock_disponible:
                raise UserError(f"No hay suficiente stock para el producto {linea.producto_id.name}. "
                               f"Solicitado: {linea.cantidad} kg, Disponible: {linea.stock_disponible} kg")
        
        try:
            # 1. Crear picking de inventario (salida)
            picking = self._create_stock_picking()
            
            # 2. Generar manifiesto de salida (SAI como generador)
            manifiesto = self._create_manifiesto_salida()
            
            # 3. Marcar como realizada
            self.write({
                'state': 'done',
                'picking_id': picking.id,
                'manifiesto_salida_id': manifiesto.id
            })
            
            _logger.info(f"Salida de acopio {self.numero_referencia} confirmada exitosamente")
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Salida Realizada',
                    'message': f'La salida de acopio {self.numero_referencia} se ha realizado exitosamente. '
                              f'Manifiesto generado: {manifiesto.numero_manifiesto}',
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            _logger.error(f"Error al confirmar salida de acopio {self.numero_referencia}: {str(e)}")
            raise UserError(f"Error al realizar la salida: {str(e)}")

    def _create_stock_picking(self):
        """
        Crea la transferencia de inventario para la salida (desde Acopio)
        """
        # Obtener ubicaciones
        location_acopio = self._get_location_acopio()
        location_customer = self.env.ref('stock.stock_location_customers')
        
        # Obtener tipo de operación de salida
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id.company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not picking_type:
            raise UserError("No se encontró un tipo de operación de salida configurado.")
        
        # Crear picking
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': location_acopio.id,
            'location_dest_id': location_customer.id,
            'origin': f"Salida Acopio: {self.numero_referencia}",
            'move_type': 'direct',
            'company_id': self.company_id.id,
            'partner_id': self.destinatario_id.id,
            'salida_acopio_id': self.id,  # Campo que agregaremos al picking
        }
        
        picking = self.env['stock.picking'].create(picking_vals)
        
        # Crear movimientos para cada línea
        for linea in self.linea_ids:
            move_vals = {
                'name': f"Salida Acopio: {linea.producto_id.name}",
                'product_id': linea.producto_id.id,
                'product_uom_qty': linea.cantidad,
                'product_uom': linea.producto_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': location_acopio.id,
                'location_dest_id': location_customer.id,
                'company_id': self.company_id.id,
            }
            
            move = self.env['stock.move'].create(move_vals)
            
            # Si hay lote disponible, crear move line con lote
            if linea.lote_id:
                move_line_vals = {
                    'move_id': move.id,
                    'product_id': linea.producto_id.id,
                    'lot_id': linea.lote_id.id,
                    'quantity': linea.cantidad,
                    'product_uom_id': linea.producto_id.uom_id.id,
                    'location_id': location_acopio.id,
                    'location_dest_id': location_customer.id,
                }
                self.env['stock.move.line'].create(move_line_vals)
        
        # Confirmar y procesar picking
        picking.action_confirm()
        picking.action_assign()
        
        # MEJORADO: Validar automáticamente solo si NO hay lotes o todos los lotes están asignados
        can_validate = True
        for move in picking.move_ids:
            if move.product_id.tracking in ['lot', 'serial']:
                # Si el producto requiere lotes, verificar que tenga move_lines
                if not move.move_line_ids:
                    can_validate = False
                    break
        
        if can_validate:
            picking.button_validate()
        
        return picking

    def _create_manifiesto_salida(self):
        """
        Crea el manifiesto ambiental donde SAI es el generador
        """
        # Obtener datos de SAI (la empresa actual)
        sai_company = self.company_id
        
        # Crear el manifiesto con SAI como generador
        manifiesto_vals = {
            # Datos del generador (SAI)
            'generador_id': False,  # No hay partner específico, es la empresa
            'numero_registro_ambiental': sai_company.vat or '',  # Usar el RFC/VAT como registro
            'generador_nombre': sai_company.name,
            'generador_codigo_postal': sai_company.zip or '',
            'generador_calle': sai_company.street or '',
            'generador_colonia': sai_company.street2 or '',
            'generador_municipio': sai_company.city or '',
            'generador_estado': sai_company.state_id.name if sai_company.state_id else '',
            'generador_telefono': sai_company.phone or '',
            'generador_email': sai_company.email or '',
            'generador_responsable_nombre': self.env.user.name,
            
            # Datos del transportista
            'transportista_id': self.transportista_id.id,
            'transportista_nombre': self.transportista_id.name,
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
            
            # Datos del destinatario
            'destinatario_id': self.destinatario_id.id,
            'destinatario_nombre': self.destinatario_id.name,
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
            
            # Información adicional
            'instrucciones_especiales': self.observaciones or '',
            'state': 'confirmed',  # Crear confirmado
            'company_id': self.company_id.id,
        }
        
        manifiesto = self.env['manifiesto.ambiental'].create(manifiesto_vals)
        
        # Crear residuos en el manifiesto basados en las líneas de salida
        for linea in self.linea_ids:
            residuo_vals = {
                'manifiesto_id': manifiesto.id,
                'product_id': linea.producto_id.id,
                'nombre_residuo': linea.producto_id.name,
                'cantidad': linea.cantidad,
                
                # Copiar clasificaciones CRETIB del producto si existen
                'clasificacion_corrosivo': getattr(linea.producto_id, 'clasificacion_corrosivo', False),
                'clasificacion_reactivo': getattr(linea.producto_id, 'clasificacion_reactivo', False),
                'clasificacion_explosivo': getattr(linea.producto_id, 'clasificacion_explosivo', False),
                'clasificacion_toxico': getattr(linea.producto_id, 'clasificacion_toxico', False),
                'clasificacion_inflamable': getattr(linea.producto_id, 'clasificacion_inflamable', False),
                'clasificacion_biologico': getattr(linea.producto_id, 'clasificacion_biologico', False),
                
                # Información del envase
                'envase_tipo': getattr(linea.producto_id, 'envase_tipo_default', ''),
                'envase_capacidad': getattr(linea.producto_id, 'envase_capacidad_default', 0),
                'etiqueta_si': True,
                'etiqueta_no': False,
            }
            
            residuo = self.env['manifiesto.ambiental.residuo'].create(residuo_vals)
            
            # Asignar lote al residuo si existe
            if linea.lote_id:
                residuo.lot_id = linea.lote_id.id
        
        return manifiesto

    def _get_location_acopio(self):
        """
        Obtiene la ubicación de acopio
        """
        location_acopio = self.env['stock.location'].search([
            ('name', '=', 'Acopio'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not location_acopio:
            raise UserError("No se encontró la ubicación 'Acopio'. Debe existir para poder realizar salidas.")
        
        return location_acopio

    def action_cancelar(self):
        """
        Cancela la salida de acopio
        """
        self.ensure_one()
        
        if self.state == 'done':
            raise UserError("No se puede cancelar una salida ya realizada.")
        
        self.state = 'cancel'

    def action_view_picking(self):
        """
        Acción para ver la transferencia de inventario asociada
        """
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
        """
        Acción para ver el manifiesto de salida generado
        """
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
        'salida.acopio',
        string='Salida de Acopio',
        required=True,
        ondelete='cascade'
    )
    
    # CORREGIDO: Remover domain restrictivo, ahora se filtra en la vista
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
        string='Cantidad (kg)',
        required=True,
        digits=(12, 3)
    )
    
    stock_disponible = fields.Float(
        string='Stock Disponible',
        compute='_compute_stock_disponible',
        help='Cantidad disponible en la ubicación Acopio'
    )
    
    clasificaciones_cretib = fields.Char(
        string='Clasificaciones CRETIB',
        compute='_compute_clasificaciones_cretib',
        readonly=True
    )

    @api.depends('producto_id')
    def _compute_clasificaciones_cretib(self):
        for record in self:
            if record.producto_id and hasattr(record.producto_id, 'get_clasificaciones_cretib'):
                record.clasificaciones_cretib = record.producto_id.get_clasificaciones_cretib()
            else:
                record.clasificaciones_cretib = ''

    @api.depends('producto_id', 'lote_id')
    def _compute_stock_disponible(self):
        for record in self:
            if record.producto_id:
                try:
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
                        
                        if record.lote_id:
                            domain.append(('lot_id', '=', record.lote_id.id))
                        
                        quants = self.env['stock.quant'].search(domain)
                        record.stock_disponible = sum(quants.mapped('quantity'))
                    else:
                        record.stock_disponible = 0.0
                except:
                    record.stock_disponible = 0.0
            else:
                record.stock_disponible = 0.0

    @api.onchange('producto_id')
    def _onchange_producto_id(self):
        """Limpiar lote cuando cambia el producto"""
        if self.producto_id:
            self.lote_id = False
            # No limpiar cantidad automáticamente para permitir edición manual
        
    @api.constrains('cantidad', 'stock_disponible')
    def _check_cantidad_disponible(self):
        for record in self:
            if record.cantidad > 0 and record.cantidad > record.stock_disponible:
                raise UserError(f"La cantidad a dar de salida ({record.cantidad} kg) no puede ser mayor "
                               f"al stock disponible ({record.stock_disponible} kg) para el producto "
                               f"{record.producto_id.name}")