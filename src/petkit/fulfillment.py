"""Atomic full-order mock fulfillment; quantity is derived from paid order lines."""
from datetime import date
from .settings import AS_OF

def plan(conn,event):
    order=conn.execute('SELECT paid_date,region_id FROM fact_order WHERE order_id=?',[event.order_id]).fetchone()
    warehouse=conn.execute('SELECT region_id FROM dim_warehouse WHERE warehouse_id=?',[event.warehouse_id]).fetchone()
    if not order or not warehouse: raise ValueError('发货引用未知订单或仓库')
    if warehouse[0]!=order[1]: raise ValueError('不支持跨区域仓库发货')
    if not order[0]<=event.ship_date<=date.fromisoformat(AS_OF): raise ValueError('发货日期超出支付日和观察截止日')
    devices=conn.execute('SELECT d.device_id,d.manufactured_date FROM fact_order_item i JOIN bridge_order_device b ON i.item_id=b.item_id JOIN dim_device d ON b.device_id=d.device_id WHERE i.order_id=?',[event.order_id]).fetchall()
    if any(day>event.ship_date for _,day in devices): raise ValueError('发货早于设备生产')
    # Existing lifecycle facts must remain chronologically possible after backfill.
    for device,_ in devices:
        for table,column in [('fact_device_activation','activation_date'),('fact_service_ticket','created_date')]:
            if conn.execute('SELECT 1 FROM '+table+' WHERE device_id=? AND '+column+'<?',[device,event.ship_date]).fetchone(): raise ValueError('发货晚于已有激活或售后')
    items=conn.execute('SELECT sku_id,SUM(quantity) FROM fact_order_item WHERE order_id=? GROUP BY sku_id ORDER BY sku_id',[event.order_id]).fetchall()
    if not items or any(q<=0 for _,q in items): raise ValueError('发货订单没有有效明细')
    return [(event.shipment_id+':stock:'+sku,sku,event.warehouse_id,event.ship_date,-int(q)) for sku,q in items]

def check_inventory(conn):
    negative=conn.execute('SELECT 1 FROM (SELECT SUM(SUM(quantity)) OVER(PARTITION BY sku_id,warehouse_id ORDER BY movement_date ROWS UNBOUNDED PRECEDING) balance FROM fact_stock_movement GROUP BY sku_id,warehouse_id,movement_date) WHERE balance<0 LIMIT 1').fetchone()
    if negative: raise ValueError('库存不足：出库导致当日或后续日期余额为负')

def apply(conn,event):
    from .ingestion import unique
    unique(conn,'fact_shipment','shipment_id',event.shipment_id)
    if conn.execute('SELECT 1 FROM fact_shipment WHERE order_id=?',[event.order_id]).fetchone(): raise ValueError('订单已经发货，不支持拆单或重复发货')
    rows=plan(conn,event)
    conn.execute('INSERT INTO fact_shipment VALUES(?,?,?,?)',[event.shipment_id,event.order_id,event.warehouse_id,event.ship_date])
    for row in rows:
        unique(conn,'fact_stock_movement','movement_id',row[0])
        conn.execute('INSERT INTO fact_stock_movement VALUES(?,?,?,?,?)',list(row))
    check_inventory(conn)
    refresh_snapshots(conn,rows)

def refresh_snapshots(conn,rows):
    for _,sku,warehouse,day,_ in rows:
        conn.execute('UPDATE fact_inventory_day SET quantity=(SELECT COALESCE(SUM(m.quantity),0) FROM fact_stock_movement m WHERE m.sku_id=fact_inventory_day.sku_id AND m.warehouse_id=fact_inventory_day.warehouse_id AND m.movement_date<=fact_inventory_day.stock_date) WHERE sku_id=? AND warehouse_id=? AND stock_date>=?',[sku,warehouse,day])
        if not conn.execute('SELECT 1 FROM fact_inventory_day WHERE stock_date=? AND sku_id=? AND warehouse_id=?',[day,sku,warehouse]).fetchone():
            balance=conn.execute('SELECT COALESCE(SUM(quantity),0) FROM fact_stock_movement WHERE sku_id=? AND warehouse_id=? AND movement_date<=?',[sku,warehouse,day]).fetchone()[0]
            conn.execute('INSERT INTO fact_inventory_day VALUES(?,?,?,?)',[day,sku,warehouse,balance])

def verify(conn,event):
    expected=(event.shipment_id,event.order_id,event.warehouse_id,event.ship_date)
    actual=conn.execute('SELECT * FROM fact_shipment WHERE order_id=?',[event.order_id]).fetchall()
    if actual!=[expected]: raise ValueError('发货来源与物流记录不一致')
    for row in plan(conn,event):
        actual=conn.execute('SELECT * FROM fact_stock_movement WHERE movement_id=?',[row[0]]).fetchall()
        if actual!=[row]: raise ValueError('发货来源与出库流水不一致')
        _,sku,warehouse,day,_=row
        snapshot=conn.execute('SELECT quantity FROM fact_inventory_day WHERE stock_date=? AND sku_id=? AND warehouse_id=?',[day,sku,warehouse]).fetchall()
        balance=conn.execute('SELECT COALESCE(SUM(quantity),0) FROM fact_stock_movement WHERE sku_id=? AND warehouse_id=? AND movement_date<=?',[sku,warehouse,day]).fetchone()[0]
        if snapshot!=[(balance,)]: raise ValueError('发货当日库存快照不一致')
