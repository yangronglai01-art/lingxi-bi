"""Execution-enforced regional views; raw database is never exposed by API.

DuckDB 通过会话级 TEMP VIEW 影子表实现行级策略；StarRocks 不支持会话临时视图，
等价物改为在 SQL AST 层把受限表改写为带区域谓词的派生表（等价于 RLS 视图）。
"""
import re

PUBLIC={'dim_product','dim_sku','dim_channel','dim_firmware','dim_fault','dim_calendar','fact_product_price','fact_product_cost'}
REGIONAL={'dim_region','dim_customer','dim_device','dim_warehouse','dim_employee','fact_order','fact_order_item',
 'fact_payment','fact_refund','fact_shipment','bridge_order_device','fact_device_activation','fact_device_binding',
 'fact_device_day','fact_device_event','fact_firmware_upgrade','fact_service_ticket','fact_ticket_status',
 'fact_repair','fact_replacement','fact_feedback','fact_inventory_day','fact_stock_movement',
 'fact_subscription','fact_app_session','fact_support_contact','fact_receipt'}

def eligible(table): return table['name'] in PUBLIC|REGIONAL

def _validate_regions(principal):
    if not principal.regions: return
    if not principal.managed: raise PermissionError('区域权限必须来自持久身份库')
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r) for r in principal.regions):
        raise PermissionError('区域编码无效')

def predicates_for(principal, raw):
    """构建 {table_name: predicate_sql} 区域行级谓词。

    raw(name) -> 原始表全限定引用。DuckDB 传 `database.main."name"`（避免 TEMP VIEW
    自我遮蔽），StarRocks 传 `` `name` ``（连接已限定到目标库，裸名即可）。
    """
    if not principal.regions: return {}
    _validate_regions(principal)
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
    return predicates

def install(conn,principal,tables):
    """DuckDB：会话级 TEMP VIEW 影子表实现行级策略。"""
    if not principal.regions: return
    _validate_regions(principal)
    database=conn.execute('SELECT current_database()').fetchone()[0]
    database='"'+database.replace('"','""')+'"'
    raw=lambda name: f'{database}.main."{name}"'
    predicates=predicates_for(principal,raw)
    for name in tables:
        if name not in predicates: raise PermissionError('该表没有批准的区域行权限策略：'+name)
        conn.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM {raw(name)} WHERE {predicates[name]}')

def rewrite(sql,principal,dialect="starrocks"):
    """StarRocks：把受限表改写为带区域谓词的派生表（等价 RLS 视图），返回改写后的 SQL。"""
    if not principal.regions: return sql
    import sqlglot
    from sqlglot import exp
    raw=lambda name: f'`{name}`'
    predicates=predicates_for(principal,raw)
    tree=sqlglot.parse_one(sql,read=dialect)
    for table in list(tree.find_all(exp.Table)):
        name=table.name.lower() if table.name else None
        if name is None or name not in predicates: continue
        pred=predicates[name]
        if pred=='TRUE': continue
        inner=sqlglot.parse_one(f'SELECT * FROM {raw(name)} WHERE {pred}',read=dialect)
        subq=exp.Subquery(this=inner)
        if table.alias: subq=subq.as_(table.alias)
        table.replace(subq)
    return tree.sql(dialect=dialect)
