{
    'name': 'Salida Acopio Manifiesto',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Salida automática de residuos del inventario hacia disposición final con manifiestos de salida',
    'description': '''
        Salida Acopio para Manifiestos Ambientales
        ==========================================
        
        Este módulo permite realizar salidas automáticas de residuos del inventario
        hacia su disposición final, generando manifiestos donde SAI es el generador.
    ''',
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['base', 'stock', 'manifiesto_ambiental'],
    'data': [
        'security/ir.model.access.csv',
        'data/stock_data.xml',
        'reports/manifiesto_salida_report.xml',
        'wizard/salida_acopio_wizard_views.xml',
        'views/salida_acopio_views.xml',
        'views/salida_acopio_print_views.xml',
        'views/stock_picking_views.xml',
        'views/salida_acopio_menus.xml',
    ],
    'demo': [],
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}