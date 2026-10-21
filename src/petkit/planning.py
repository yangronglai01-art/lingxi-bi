"""Approved deterministic demo questions; no silent rule fallback in LLM mode."""
import re
from datetime import date
from .settings import AS_OF
from .catalog import METRICS


def normalize(text): return re.sub(r"[\s？?，,。！!]","",text)


def sales_sql(group="model",month=None):
    dimensions={"model":"p.product_name", "channel":"c.channel_name", "month":"strftime(o.paid_date, '%Y-%m')"}
    dim=dimensions[group]
    where=f"WHERE strftime(o.paid_date, '%Y-%m') = '{month}'" if month else ""
    return f"""WITH refunds AS (
        SELECT item_id, SUM(amount) AS refunded FROM fact_refund GROUP BY item_id
    ) SELECT {dim} AS dimension, ROUND(SUM(i.amount-COALESCE(r.refunded,0)),2) AS net_sales
    FROM fact_order_item i JOIN fact_order o ON i.order_id=o.order_id
    JOIN dim_sku s ON i.sku_id=s.sku_id JOIN dim_product p ON s.product_id=p.product_id
    JOIN dim_channel c ON o.channel_id=c.channel_id
    LEFT JOIN refunds r ON i.item_id=r.item_id {where}
    GROUP BY {dim} ORDER BY {('dimension' if group=='month' else 'net_sales DESC')}"""


def cohort_sql(kind,month=None):
    target="fact_device_activation" if kind=="activation30" else "fact_service_ticket"
    event="activation_date" if kind=="activation30" else "created_date"
    where=f"AND strftime(o.paid_date, '%Y-%m') = '{month}'" if month else ""
    return f"""WITH cohort AS (
      SELECT b.device_id, d.product_id, o.paid_date FROM bridge_order_device b
      JOIN fact_order_item i ON b.item_id=i.item_id JOIN fact_order o ON i.order_id=o.order_id
      JOIN dim_device d ON b.device_id=d.device_id
      WHERE o.paid_date <= DATE '{AS_OF}' - INTERVAL '30 days' {where}
    ), outcomes AS (
      SELECT DISTINCT c.device_id FROM cohort c JOIN {target} e ON c.device_id=e.device_id
      WHERE e.{event} BETWEEN c.paid_date AND c.paid_date + INTERVAL '30 days'
    ) SELECT p.product_name, COUNT(DISTINCT c.device_id) AS devices,
      COUNT(DISTINCT e.device_id) AS matched_devices,
      ROUND(100.0*COUNT(DISTINCT e.device_id)/NULLIF(COUNT(DISTINCT c.device_id),0),2) AS rate_percent
    FROM cohort c JOIN dim_product p ON c.product_id=p.product_id
    LEFT JOIN outcomes e ON c.device_id=e.device_id GROUP BY p.product_name ORDER BY rate_percent DESC"""


def template_sql(metric,group="model",month=None):
    if metric=="net_sales": return sales_sql(group,month)
    if metric in {"activation30","service30"}: return cohort_sql(metric,month)
    if metric=="offline_rate":
        dim="f.firmware_name" if group=="firmware" else "p.product_name"
        where=f"WHERE strftime(x.event_date, '%Y-%m')='{month}'" if month else ""
        return f"""SELECT {dim} AS dimension, COUNT(*) AS device_days,
          ROUND(100.0*SUM(CASE WHEN x.online=0 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS offline_percent
          FROM fact_device_day x JOIN dim_device d ON x.device_id=d.device_id
          JOIN dim_product p ON d.product_id=p.product_id JOIN dim_firmware f ON x.firmware_id=f.firmware_id
          {where} GROUP BY {dim} ORDER BY offline_percent DESC"""
    if metric=="tickets":
        dim="f.fault_name" if group=="fault" else "p.product_name"
        where=f"WHERE strftime(t.created_date, '%Y-%m')='{month}'" if month else ""
        return f"""SELECT {dim} AS dimension,COUNT(*) AS tickets FROM fact_service_ticket t
          JOIN dim_device d ON t.device_id=d.device_id JOIN dim_product p ON d.product_id=p.product_id
          JOIN dim_fault f ON t.fault_id=f.fault_id {where} GROUP BY {dim} ORDER BY tickets DESC"""
    if metric=="received_units":
        where=f"WHERE strftime(r.receipt_date, '%Y-%m')='{month}'" if month else ""
        return f"""SELECT w.warehouse_name,SUM(r.quantity) AS received_units FROM fact_receipt r
          JOIN dim_warehouse w ON r.warehouse_id=w.warehouse_id
          {where} GROUP BY w.warehouse_name ORDER BY received_units DESC"""
    if metric=="inventory":
        # Latest snapshot per SKU and warehouse; updates need not arrive together.
        restriction=f"AND strftime(y.stock_date, '%Y-%m')='{month}'" if month else ""
        return f"""SELECT p.product_name,SUM(x.quantity) AS stock_quantity FROM fact_inventory_day x
          JOIN dim_sku s ON x.sku_id=s.sku_id JOIN dim_product p ON s.product_id=p.product_id
          WHERE x.stock_date=(SELECT MAX(y.stock_date) FROM fact_inventory_day y
            WHERE y.sku_id=x.sku_id AND y.warehouse_id=x.warehouse_id {restriction})
          GROUP BY p.product_name ORDER BY stock_quantity DESC"""
    raise ValueError("没有批准的离线模板")


def examples():
    result=[]
    base=[("各型号净销售额","net_sales","model"),("各渠道净销售额","net_sales","channel"),
          ("各型号售出30天激活率","activation30","model"),
          ("各型号售出30天售后设备率","service30","model"),
          ("各固件版本设备离线率","offline_rate","firmware"),
          ("各故障分类工单数","tickets","fault"),("各仓库采购收货数量","received_units","warehouse")]
    for prefix,month in [("",None),("2026年9月","2026-09"),("2026年8月","2026-08"),
                         ("2026年3月","2026-03"),("2026年6月","2026-06")]:
        for text,metric,group in base:
            question=prefix+text
            result.append(dict(question=question,metric_id=metric,group=group,month=month,
                               sql=template_sql(metric,group,month)))
    result.extend([
        dict(question="每月净销售额趋势",metric_id="net_sales",group="month",month=None,sql=sales_sql("month")),
        dict(question="各型号期末库存",metric_id="inventory",group="model",month=None,sql=template_sql("inventory")),
    ])
    return result


def plan(question,demo=False):
    registry={normalize(x["question"]):x for x in examples()}
    approved=registry.get(normalize(question))
    if approved:
        metric=next(m for m in METRICS if m["id"]==approved["metric_id"])
        return dict(status="ready",metric=metric,group=approved["group"],month=approved["month"],
                    sql=approved["sql"] if demo else None,assumptions=[metric["definition"]])
    if "激活率" in question and "30天" not in question:
        return dict(status="clarification",message="请确认：按售出后30天内激活设备数/观察满30天售出设备数计算吗？示例：各型号售出30天激活率")
    if ("售后率" in question or "故障率" in question) and "30天" not in question:
        return dict(status="clarification",message="请明确是工单数、售后设备数还是售出30天售后设备率；三者口径不同。")
    if demo:
        return dict(status="unsupported",message="离线模板仅支持批准的示例问题；请在示例列表中选择，或配置真实模型后使用llm模式。")
    # Preserve the registered 30-day definition when the user omits "售出".
    # This is a spelling variant, not a new metric or an inferred time window.
    matched=[m for m in METRICS if any(a in question for a in
        [m["name"]]+m["aliases"]+([m["name"].removeprefix("售出")]
        if m["id"] in {"activation30","service30"} else []))]
    if len(matched)>1:
        return dict(status="clarification",message="本次问题涉及多个指标，请先拆成一个明确指标的问题。")
    return dict(status="ready",metric=matched[0] if matched else None,
                group="由问题决定",month=None,sql=None,
                assumptions=[matched[0]["definition"]] if matched else ["没有批准指标命中，需要模型明确计划，不得臆造业务口径"])
