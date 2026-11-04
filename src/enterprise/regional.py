"""Independent Python oracle for the approved regional snapshot policy.

It intentionally does not execute or interpret the SQL predicate implementation.
Unknown tables have no regional exposure. Missing lineage fails publication.
"""
from src.petkit.row_policy import PUBLIC,REGIONAL


def subset(rows,regions):
    regions=set(regions)
    order_derived={'fact_order_item','fact_payment','fact_shipment','fact_refund','bridge_order_device'}
    device_derived={'dim_device','fact_device_activation','fact_device_binding','fact_device_day','fact_device_event','fact_firmware_upgrade','fact_service_ticket'}
    ticket_derived={'fact_ticket_status','fact_repair','fact_replacement','fact_feedback','fact_support_contact','dim_employee'}
    if (set(rows)&(order_derived|device_derived|ticket_derived)) and 'fact_order' not in rows:raise ValueError('Regional order lineage missing')
    if (set(rows)&(device_derived|ticket_derived)) and not {'fact_order_item','bridge_order_device'}<=rows.keys():raise ValueError('Regional device lineage missing')
    if (set(rows)&ticket_derived) and 'fact_service_ticket' not in rows:raise ValueError('Regional ticket lineage missing')
    if (set(rows)&{'fact_inventory_day','fact_stock_movement','fact_receipt'}) and 'dim_warehouse' not in rows:raise ValueError('Regional warehouse lineage missing')
    if (set(rows)&{'fact_subscription','fact_app_session'}) and 'dim_customer' not in rows:raise ValueError('Regional customer lineage missing')
    orders={r['order_id'] for r in rows.get('fact_order',[]) if r['region_id'] in regions}
    items={r['item_id'] for r in rows.get('fact_order_item',[]) if r['order_id'] in orders}
    devices={r['device_id'] for r in rows.get('bridge_order_device',[]) if r['item_id'] in items}
    tickets={r['ticket_id'] for r in rows.get('fact_service_ticket',[]) if r['device_id'] in devices}
    employees={r['employee_id'] for r in rows.get('fact_service_ticket',[]) if r['ticket_id'] in tickets}
    warehouses={r['warehouse_id'] for r in rows.get('dim_warehouse',[]) if r['region_id'] in regions}
    customers={r['customer_id'] for r in rows.get('dim_customer',[]) if r['region_id'] in regions}
    rules={n:(lambda r:True) for n in PUBLIC}
    for n in ('dim_region','dim_customer','dim_warehouse','fact_order'):rules[n]=lambda r:r['region_id'] in regions
    for n in ('fact_order_item','fact_payment','fact_shipment'):rules[n]=lambda r:r['order_id'] in orders
    for n in ('fact_refund','bridge_order_device'):rules[n]=lambda r:r['item_id'] in items
    for n in device_derived:rules[n]=lambda r:r['device_id'] in devices
    for n in ticket_derived-{'dim_employee'}:rules[n]=lambda r:r['ticket_id'] in tickets
    for n in ('fact_inventory_day','fact_stock_movement','fact_receipt'):rules[n]=lambda r:r['warehouse_id'] in warehouses
    for n in ('fact_subscription','fact_app_session'):rules[n]=lambda r:r['customer_id'] in customers
    rules['dim_employee']=lambda r:r['employee_id'] in employees
    return {n:[r for r in records if rules[n](r)] for n,records in rows.items() if n in PUBLIC|REGIONAL}
