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