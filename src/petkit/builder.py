"""Seeded, referentially consistent simulation. Build is separate from evaluation."""
import json
import os
import random
import uuid
from collections import defaultdict
from datetime import date, timedelta

from .catalog import catalog_payload
from .settings import AS_OF, CURRENT, DATA, END, SEED, START, VERSION


def generate(orders=3000, extension_periods=12):
    if orders < 10 or not 1 <= extension_periods <= 12:
        raise ValueError("orders必须>=10，extension_periods在1到12之间")
    rng = random.Random(SEED)
    rows = defaultdict(list)
    def add(name, *values): rows[name].append(values)
    start, end, asof = map(date.fromisoformat, [START, END, AS_OF])
    products = []
    for i, cat in enumerate(["喂食器", "饮水机", "猫砂盆"]):
        for j in range(4):
            pid = f"P{i*4+j+1:03}"
            price = [399,199,1299][i] + j*100
            products.append((pid,price))
            add("dim_product",pid,f"模拟{cat}{j+1}型",cat,price)
            for k in range(2):
                sid=f"{pid}-S{k+1}"
                add("dim_sku",sid,pid,f"模拟{cat}{j+1}型规格{k+1}")
                add("fact_product_price",sid,start,price)
                add("fact_product_cost",sid,start,round(price*.58,2))
    for i,n in enumerate(["自营商城","电商平台","经销渠道","线下零售"],1): add("dim_channel",f"CH{i}",n)
    for i,n in enumerate(["华东","华南","华北","西南"],1):
        add("dim_region",f"R{i}",n)
        add("dim_warehouse",f"W{i}",f"模拟{n}仓",f"R{i}")
    for i in range(1,301): add("dim_customer",f"C{i:04}",f"虚拟客户{i}",f"R{(i-1)%4+1}")
    for i in range(1,7):
        add("dim_supplier",f"S{i}",f"模拟供应商{i}")
        add("dim_employee",f"E{i}",f"模拟客服{i}")
    for i,n in enumerate(["连接异常","传感器异常","电机异常","咨询使用"],1): add("dim_fault",f"F{i}",n)
    for i in range(1,4): add("dim_firmware",f"V{i}",f"模拟固件{i}.0",start)
    for d in range((asof-start).days+1):
        dt=start+timedelta(days=d)
        add("dim_calendar",dt,dt.strftime("%Y-%m"),(dt.month-1)//3+1)
    device_number=ticket_number=0
    for i in range(orders):
        oid=f"O{i+1:07}"
        cid=rng.randint(1,300)
        paid=start+timedelta(days=rng.randrange((end-start).days+1))
        ch=f"CH{rng.choices([1,2,3,4],[4,5,2,1])[0]}"
        amount=0
        for line in range(rng.choice([1,1,1,2])):
            pid, price=rng.choice(products)
            sid=f"{pid}-S{rng.randint(1,2)}"; item=f"{oid}-L{line+1}"
            qty=rng.choice([1,1,1,2,3]); value=round(price*qty*rng.choice([1,.95,.9]),2)
            amount+=value
            add("fact_order_item",item,oid,sid,qty,value,round(price*qty*.58,2))
            if rng.random() < (.13 if pid=="P003" else .04):
                refund_date=paid+timedelta(days=rng.randint(1,14))
                if refund_date<=asof: add("fact_refund",f"RF-{item}",item,refund_date,round(value*rng.choice([.5,1]),2))
            for _ in range(qty):
                device_number+=1; device=f"D{device_number:08}"
                add("dim_device",device,pid,paid-timedelta(days=rng.randint(10,90)))
                add("bridge_order_device",device,item)
                active_date=None
                if rng.random()<.87:
                    dt=paid+timedelta(days=rng.randint(1,40))
                    if dt<=asof:
                        active_date=dt
                        add("fact_device_activation",device,dt)
                        add("fact_device_binding",device,f"C{cid:04}",dt)
                if active_date:
                    last_observed_day=min(14,(asof-active_date).days)
                    for day in range(0, last_observed_day+1):
                        dt=active_date+timedelta(days=day)
                        fw=f"V{(device_number%3)+1}"
                        online=int(rng.random()>(.14 if fw=="V2" else .035))
                        active=int(online and rng.random()<.8)
                        observed_firmware="V3" if day==last_observed_day else fw
                        add("fact_device_day",device,dt,observed_firmware,online,active,rng.randint(1,8) if active else 0)
                        if active and day<2:
                            category_index=(int(pid[1:])-1)//4
                            add("fact_device_event",f"EV-{device}-{day}",device,dt,["喂食","饮水","如厕"][category_index])
                    add("fact_firmware_upgrade",f"UP-{device}",device,"V3",active_date+timedelta(days=last_observed_day),1)
                if rng.random()<(.17 if pid=="P003" else .055):
                    for attempt in range(rng.choice([1,1,2])):
                        created=paid+timedelta(days=rng.randint(3,50))
                        if created>asof: continue
                        ticket_number+=1; tid=f"T{ticket_number:07}"
                        employee=f"E{rng.randint(1,6)}"
                        add("fact_service_ticket",tid,device,f"F{rng.randint(1,4)}",employee,created,"已创建")
                        add("fact_ticket_status",f"TS-{tid}",tid,created,"已创建")
                        add("fact_support_contact",f"CO-{tid}",tid,employee,created)
                        resolved=created+timedelta(days=2)
                        if resolved<=asof:
                            current=rows["fact_service_ticket"][-1]
                            rows["fact_service_ticket"][-1]=(*current[:-1],"已解决")
                            add("fact_repair",f"RE-{tid}",tid,resolved,round(rng.uniform(20,120),2))
                            add("fact_ticket_status",f"TS-{tid}-2",tid,resolved,"已解决")
                            add("fact_feedback",f"FB-{tid}",tid,rng.randint(2,5),resolved)
                            if ticket_number%11==0:
                                new_device=f"X{ticket_number:08}"
                                add("dim_device",new_device,pid,paid-timedelta(days=10))
                                add("fact_replacement",f"RP-{tid}",tid,device,new_device,resolved)
        add("fact_order",oid,f"C{cid:04}",ch,f"R{(cid-1)%4+1}",paid,round(amount,2),"已支付")
        add("fact_payment",f"PAY-{oid}",oid,paid,round(amount,2))
        if paid+timedelta(days=1)<=asof:
            add("fact_shipment",f"SH-{oid}",oid,f"W{(cid-1)%4+1}",paid+timedelta(days=1))
    # Actual stock snapshots derive from opening stock and the recorded movements.
    for sid,pid,_ in rows["dim_sku"]:
        for w in range(1,5):
            opening=100+rng.randint(0,100)
            add("fact_stock_movement",f"MV-{sid}-{w}-0",sid,f"W{w}",start,opening)
            balance=opening
            for m in range(12):
                dt=date(2025+(9+m)//12,(9+m)%12+1,1)
                delta=rng.randint(-10,15); balance+=delta
                add("fact_stock_movement",f"MV-{sid}-{w}-{m+1}",sid,f"W{w}",dt,delta)
                add("fact_inventory_day",dt,sid,f"W{w}",balance)
    for i in range(1,61):
        dt=start+timedelta(days=i*4); purchase=f"PO{i:04}"; item=f"PI{i:04}"
        sid=rows["dim_sku"][i%24][0]; qty=10+i
        add("fact_purchase_order",purchase,f"S{i%6+1}",dt)
        add("fact_purchase_item",item,purchase,sid,qty,qty*100)
        add("fact_receipt",f"RC{i:04}",item,f"W{i%4+1}",dt+timedelta(days=3),qty)
    for i,(pid,_) in enumerate(products):
        campaign=f"CA{i:03}"; dt=start+timedelta(days=i*10)
        add("fact_campaign",campaign,pid,dt,dt+timedelta(days=6),1000+i*100)
        for j in range(7):
            impressions=rng.randint(1000,5000); clicks=rng.randint(50,150)
            add("fact_campaign_day",campaign,dt+timedelta(days=j),impressions,clicks,round(clicks*.4,2))
    for i in range(1,121):
        dt=start+timedelta(days=i*2)
        add("fact_subscription",f"SU{i:04}",f"C{i:04}",dt,min(dt+timedelta(days=30),asof),19.9)
        add("fact_app_session",f"SE{i:04}",f"C{i:04}",dt,rng.randint(10,600))
    return rows


def build(orders=3000, extension_periods=12, publish=True):
    if publish and CURRENT.exists() and (DATA/"control.sqlite3").exists():
        raise ValueError("业务实例已初始化，请构建publish=False候选版本并通过发布门禁")
    import duckdb
    import pandas as pd
    payload=catalog_payload()
    rows=generate(orders,extension_periods)
    # Closed enums are complete for this synthetic snapshot, not guessed translations.
    enum_fields={"status","category","channel_name","region_name","firmware_name","fault_name","event_type"}
    for name,t in payload["tables"].items():
        if not t["core"]: continue
        for i,(column,metadata) in enumerate(t["columns"].items()):
            if column in enum_fields:
                values=sorted({row[i] for row in rows[name] if row[i] is not None})
                if values:
                    metadata["enum"]=values
                    metadata["enum_scope"]="complete_synthetic_snapshot"
    generation=f"{VERSION}-{uuid.uuid4().hex[:10]}"
    folder=DATA/generation; folder.mkdir(parents=True)
    db=folder/"warehouse.duckdb"
    counts={}
    conn=duckdb.connect(str(db))
    try:
        conn.execute("BEGIN")
        for name,t in payload["tables"].items():
            ddl=", ".join(f'"{k}" {v["type"]}' for k,v in t["columns"].items())
            conn.execute(f'CREATE TABLE "{name}" ({ddl})')
            if t["core"]:
                data=rows[name]
            else:
                # Long-tail themed analytical fixtures; never passed off as core business metrics.
                offset=sum(ord(c) for c in name)%91
                data=[]
                for i in range(extension_periods):
                    dt=(date(2025+(9+i)//12,(9+i)%12+1,1) if name.endswith("monthly")
                        else date.fromisoformat(START)+timedelta(days=i))
                    for pid,_,_,_ in rows["dim_product"]:
                        count=offset+i+int(pid[1:])+1
                        data.append((dt,pid,count,round(count*(1+offset/10),2),VERSION))
            if data:
                frame=pd.DataFrame(data,columns=list(t["columns"]))
                conn.register("_fixture",frame)
                conn.execute(f'INSERT INTO "{name}" SELECT * FROM _fixture')
                conn.unregister("_fixture")
            counts[name]=len(data)
        conn.execute("COMMIT")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    # Serialize before publishing; readers bind to immutable generation folders.
    payload.update(start=START,end=END,as_of=AS_OF,seed=SEED,generation=generation)
    (folder/"catalog.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    summary=dict(generation=generation,version=VERSION,seed=SEED,synthetic=True,
        table_count=len(counts),core_tables=40,extension_tables=460,total_rows=sum(counts.values()),
        counts=counts,orders=orders,as_of=AS_OF,verification="仅构建摘要；未执行问数测试、模型评测或压测")
    (folder/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    if publish:
        from .releases import guard,write
        with guard(str(DATA)):
            if CURRENT.exists() and (DATA/"control.sqlite3").exists():
                raise ValueError("业务实例已初始化，候选版本未发布，请通过发布门禁")
            write(CURRENT,dict(generation=generation,version=VERSION))
    summary["published"]=publish
    return summary
