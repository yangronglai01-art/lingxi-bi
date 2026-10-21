"""Quantity-based mock purchasing and partial receipts; source rows stay immutable."""
from datetime import date
from .settings import START,END,AS_OF
from .ingestion_models import Purchase,Receipt
from .fulfillment import refresh_snapshots,check_inventory

def purchase_rows(conn,event):
    if not date.fromisoformat(START)<=event.purchase_date<=min(date.fromisoformat(END),date.fromisoformat(AS_OF)):
        raise ValueError('采购日期超出历史窗口')
    if not conn.execute('SELECT 1 FROM dim_supplier WHERE supplier_id=?',[event.supplier_id]).fetchone(): raise ValueError('未知供应商')
    rows=[('fact_purchase_order',(event.purchase_id,event.supplier_id,event.purchase_date))]
    for item in event.items:
        if not conn.execute('SELECT 1 FROM dim_sku WHERE sku_id=?',[item.sku_id]).fetchone(): raise ValueError('采购引用未知SKU')
        rows.append(('fact_purchase_item',(item.purchase_item_id,event.purchase_id,item.sku_id,item.quantity,float(item.amount))))
    return rows

def receipt_row(conn,event):
    row=conn.execute('SELECT i.sku_id,i.quantity,p.purchase_date FROM fact_purchase_item i JOIN fact_purchase_order p ON i.purchase_id=p.purchase_id WHERE i.purchase_item_id=?',[event.purchase_item_id]).fetchone()
    if not row: raise ValueError('收货引用未知采购行')
    if not conn.execute('SELECT 1 FROM dim_warehouse WHERE warehouse_id=?',[event.warehouse_id]).fetchone(): raise ValueError('收货引用未知仓库')
    if not row[2]<=event.receipt_date<=date.fromisoformat(AS_OF): raise ValueError('收货日期超出采购日和观察截止日')
    return (event.receipt_id+':stock',row[0],event.warehouse_id,event.receipt_date,event.quantity),row[1]

def apply(conn,event):
    from .ingestion import unique
    if isinstance(event,Purchase):
        for table,row in purchase_rows(conn,event):
            unique(conn,table,'purchase_id' if table=='fact_purchase_order' else 'purchase_item_id',row[0])
            conn.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',list(row))
    elif isinstance(event,Receipt):
        unique(conn,'fact_receipt','receipt_id',event.receipt_id)
        movement,ordered=receipt_row(conn,event)
        received=conn.execute('SELECT COALESCE(SUM(quantity),0) FROM fact_receipt WHERE purchase_item_id=?',[event.purchase_item_id]).fetchone()[0]
        if received+event.quantity>ordered: raise ValueError('累计收货超过采购数量')
        unique(conn,'fact_stock_movement','movement_id',movement[0])
        conn.execute('INSERT INTO fact_receipt VALUES(?,?,?,?,?)',[event.receipt_id,event.purchase_item_id,event.warehouse_id,event.receipt_date,event.quantity])
        conn.execute('INSERT INTO fact_stock_movement VALUES(?,?,?,?,?)',list(movement))
        check_inventory(conn)
        refresh_snapshots(conn,[movement])
    else: raise ValueError('不支持的采购事件')

def verify(conn,event):
    if isinstance(event,Purchase):
        for table,row in purchase_rows(conn,event):
            key='purchase_id' if table=='fact_purchase_order' else 'purchase_item_id'
            actual=conn.execute('SELECT * FROM '+table+' WHERE '+key+'=?',[row[0]]).fetchall()
            if len(actual)!=1 or any(abs(float(a)-b)>.001 if isinstance(b,float) else a!=b for a,b in zip(actual[0],row)): raise ValueError('采购来源与业务行不一致')
        if conn.execute('SELECT count(*) FROM fact_purchase_item WHERE purchase_id=?',[event.purchase_id]).fetchone()[0]!=len(event.items): raise ValueError('采购存在额外明细')
    elif isinstance(event,Receipt):
        row=(event.receipt_id,event.purchase_item_id,event.warehouse_id,event.receipt_date,event.quantity)
        if conn.execute('SELECT * FROM fact_receipt WHERE receipt_id=?',[event.receipt_id]).fetchall()!=[row]: raise ValueError('收货来源与业务行不一致')
        movement,_=receipt_row(conn,event)
        if conn.execute('SELECT * FROM fact_stock_movement WHERE movement_id=?',[movement[0]]).fetchall()!=[movement]: raise ValueError('收货来源与入库流水不一致')
        balance=conn.execute('SELECT COALESCE(SUM(quantity),0) FROM fact_stock_movement WHERE sku_id=? AND warehouse_id=? AND movement_date<=?',list(movement[1:4])).fetchone()[0]
        snapshot=conn.execute('SELECT quantity FROM fact_inventory_day WHERE sku_id=? AND warehouse_id=? AND stock_date=?',list(movement[1:4])).fetchall()
        if snapshot!=[(balance,)]: raise ValueError('收货当日库存快照不一致')

def check_procurement(conn):
    """Global quantity/date consistency, including the historical seed purchases."""
    invalid=conn.execute('''SELECT 1 FROM fact_purchase_item WHERE quantity<=0 OR amount<=0 OR NOT isfinite(amount)
      UNION ALL SELECT 1 FROM fact_receipt r JOIN fact_purchase_item i ON r.purchase_item_id=i.purchase_item_id
      JOIN fact_purchase_order p ON i.purchase_id=p.purchase_id WHERE r.quantity<=0 OR r.receipt_date<p.purchase_date OR r.receipt_date>?
      UNION ALL SELECT 1 FROM fact_receipt r JOIN fact_purchase_item i ON r.purchase_item_id=i.purchase_item_id
      GROUP BY r.purchase_item_id HAVING SUM(r.quantity)>MAX(i.quantity) LIMIT 1''',[date.fromisoformat(AS_OF)]).fetchone()
    if invalid: raise ValueError('采购收货数量或日期不一致')
