-e ### ./data/stock_data.xml
```
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Secuencia para salida de acopio -->
    <record id="seq_salida_acopio" model="ir.sequence">
        <field name="name">Salida de Acopio</field>
        <field name="code">salida.acopio</field>
        <field name="prefix">SAI-</field>
        <field name="padding">6</field>
        <field name="company_id" eval="False"/>
    </record>
</odoo>
```

-e ### ./models/salida_acopio.py
```
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
```

-e ### ./models/stock_picking_inherit.py
```
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
```

-e ### ./salida_modulo_completo.md
```
-e ### ./data/stock_data.xml
```
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Secuencia para salida de acopio -->
    <record id="seq_salida_acopio" model="ir.sequence">
        <field name="name">Salida de Acopio</field>
        <field name="code">salida.acopio</field>
        <field name="prefix">SAI-</field>
        <field name="padding">6</field>
        <field name="company_id" eval="False"/>
    </record>
</odoo>
```

-e ### ./models/salida_acopio.py
```
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
```

-e ### ./models/stock_picking_inherit.py
```
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
```
```

-e ### ./security/ir.model.access.csv
```
id,name,model_id:id,perm_read,perm_write,perm_create,perm_unlink
access_salida_acopio,access_salida_acopio,model_salida_acopio,1,1,1,1
access_salida_acopio_linea,access_salida_acopio_linea,model_salida_acopio_linea,1,1,1,1
access_salida_acopio_wizard,access_salida_acopio_wizard,model_salida_acopio_wizard,1,1,1,1
access_salida_acopio_wizard_linea,access_salida_acopio_wizard_linea,model_salida_acopio_wizard_linea,1,1,1,1
```

-e ### ./views/salida_acopio_menus.xml
```
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
</odoo>
```

-e ### ./views/salida_acopio_views.xml
```
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <!-- Acción principal para salida de acopio -->
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

    <!-- Acción para abrir wizard de nueva salida -->
    <record id="action_salida_acopio_wizard" model="ir.actions.act_window">
        <field name="name">Nueva Salida de Acopio</field>
        <field name="res_model">salida.acopio.wizard</field>
        <field name="view_mode">form</field>
        <field name="target">new</field>
    </record>

    <!-- Vista de lista para salida de acopio -->
    <record id="view_salida_acopio_list" model="ir.ui.view">
        <field name="name">salida.acopio.list</field>
        <field name="model">salida.acopio</field>
        <field name="arch" type="xml">
            <list string="Salidas de Acopio" default_order="fecha_salida desc">
                <header>
                    <button name="%(action_salida_acopio_wizard)d" 
                            string="Nueva Salida" 
                            type="action" 
                            class="btn-primary"/>
                </header>
                <field name="numero_referencia"/>
                <field name="transportista_id"/>
                <field name="destinatario_id"/>
                <field name="fecha_salida"/>
                <field name="usuario_salida"/>
                <field name="total_residuos"/>
                <field name="cantidad_total"/>
                <field name="manifiesto_salida_id"/>
                <field name="state" decoration-success="state == 'done'" decoration-muted="state == 'cancel'"/>
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

    <!-- Vista de formulario para salida de acopio -->
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
                            confirm="¿Está seguro de que desea confirmar esta salida? Se crearán los movimientos de inventario y el manifiesto."/>
                    
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
                            confirm="¿Está seguro de que desea cancelar esta salida?"/>
                    
                    <field name="state"
                           widget="statusbar"
                           statusbar_visible="draft,done"/>
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
                        <h1>
                            <field name="numero_referencia" readonly="1"/>
                        </h1>
                    </div>
                    
                    <!-- Información del manifiesto generado -->
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
                                    <field name="lote_id" domain="[('product_id', '=', producto_id)]" optional="show"/>
                                    <field name="stock_disponible" readonly="1"/>
                                    <field name="cantidad"/>
                                    <field name="clasificaciones_cretib"/>
                                </list>
                                <form string="Línea de Salida">
                                    <group col="2">
                                        <field name="producto_id" options="{'no_create': True}"/>
                                        <field name="lote_id" domain="[('product_id', '=', producto_id)]"/>
                                        <field name="stock_disponible" readonly="1"/>
                                        <field name="cantidad"/>
                                        <field name="clasificaciones_cretib" readonly="1"/>
                                    </group>
                                </form>
                            </field>
                        </page>
                        
                        <page string="Observaciones">
                            <group>
                                <field name="observaciones" nolabel="1" placeholder="Observaciones adicionales..." readonly="state != 'draft'"/>
                            </group>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <!-- Vista de búsqueda para salidas de acopio -->
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
                
                <group expand="0" string="Agrupar por">
                    <filter string="Estado" name="group_state" context="{'group_by':'state'}"/>
                    <filter string="Transportista" name="group_transportista" context="{'group_by':'transportista_id'}"/>
                    <filter string="Destinatario" name="group_destinatario" context="{'group_by':'destinatario_id'}"/>
                    <filter string="Usuario" name="group_user" context="{'group_by':'usuario_salida'}"/>
                    <filter string="Fecha" name="group_date" context="{'group_by':'fecha_salida:day'}"/>
                </group>
            </search>
        </field>
    </record>
</odoo>
```

-e ### ./wizard/salida_acopio_wizard_views.xml
```
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
</odoo>
```

-e ### ./wizard/salida_acopio_wizard.py
```
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
```

### __init__.py
```python
# -*- coding: utf-8 -*-
from . import models
from . import wizard
```

### __manifest__.py
```python
{
    'name': 'Salida Acopio Manifiesto',
    'version': '18.0.1.0.0',
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
    'depends': ['base', 'stock', 'manifiesto_ambiental', 'ingreso_acopio'],
    'data': [
        'security/ir.model.access.csv',
        'data/stock_data.xml',
        'wizard/salida_acopio_wizard_views.xml',
        'views/salida_acopio_views.xml',
        'views/salida_acopio_menus.xml',
    ],
    'demo': [],
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
```

