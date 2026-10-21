"""Execution-enforced regional views; raw database is never exposed by API."""
PUBLIC={'dim_product','dim_sku','dim_channel','dim_firmware','dim_fault','dim_calendar','fact_product_price','fact_product_cost'}
REGIONAL={'dim_region','dim_customer','dim_device','dim_warehouse','dim_employee','fact_order','fact_order_item',
 'fact_payment','fact_refund','fact_shipment','bridge_order_device','fact_device_activation','fact_device_binding',
 'fact_device_day','fact_device_event','fact_firmware_upgrade','fact_service_ticket','fact_ticket_status',
 'fact_repair','fact_replacement','fact_feedback','fact_inventory_day','fact_stock_movement',
 'fact_subscription','fact_app_session','fact_support_contact','fact_receipt'}

def eligible(table): return table['name'] in PUBLIC|REGIONAL

def install(conn,principal,tables):
    if not principal.regions: return
    if not principal.managed: raise PermissionError('区域权限必须来自持久身份库')
    import re
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r) for r in principal.regions):
        raise PermissionError('区域编码无效')
    database=conn.execute('SELECT current_database()').fetchone()[0]
    database='"'+database.replace('"','""')+'"'
    raw=lambda name: f'{database}.main."{name}"'
    regions=','.join("'"+r+"'" for r in principal.regions)
    orders=f'SELECT order_id FROM {raw("fact_order")} WHERE region_id IN ({regions})'
    items=f'SELECT item_id FROM {raw("fact_order_item")} WHERE order_id IN ({orders})'
    devices=f'SELECT device_id FROM {raw("bridge_order_device")} WHERE item_id IN ({items})'
    tickets=f'SELECT ticket_id FROM {raw("fact_service_ticket")} WHERE device_id IN ({devices})'
    warehouses=f'SELECT warehouse_id FROM {raw("dim_warehouse")} WHERE region_id IN ({regions})'
    customers=f'SELECT customer_id FROM {raw("dim_customer")} WHERE region_id IN ({regions})'
    predicates={n:'TRUE' for n in PUBLIC}
    predicates.update({n:f'region_id IN ({regions})' for n in ['dim_region','dim_customer','dim_warehouse','fact_order']})
    predicates.update({n:f'order_id IN ({orders})' for n in ['fact_order_item','fact_payment','fact_shipment']})
    predicates.update({n:f'item_id IN ({items})' for n in ['fact_refund','bridge_order_device']})
    predicates.update({n:f'device_id IN ({devices})' for n in ['dim_device','fact_device_activation','fact_device_binding','fact_device_day','fact_device_event','fact_firmware_upgrade','fact_service_ticket']})
    predicates.update({n:f'ticket_id IN ({tickets})' for n in ['fact_ticket_status','fact_repair','fact_replacement','fact_feedback','fact_support_contact']})
    predicates.update({n:f'warehouse_id IN ({warehouses})' for n in ['fact_inventory_day','fact_stock_movement','fact_receipt']})
    predicates.update({n:f'customer_id IN ({customers})' for n in ['fact_subscription','fact_app_session']})
    predicates['dim_employee']=f'employee_id IN (SELECT employee_id FROM {raw("fact_service_ticket")} WHERE ticket_id IN ({tickets}))'
    for name in tables:
        if name not in predicates: raise PermissionError('该表没有批准的区域行权限策略：'+name)
        conn.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM {raw(name)} WHERE {predicates[name]}')
