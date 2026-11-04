"""Executable rules for the bounded metric compiler, pinned to a data release.

These are technical contracts, not evidence of business Owner approval. New
metrics need a compiler/validator and business approval before being enabled.
"""
import hashlib
import json
from datetime import date, timedelta

from .intent import Clarify, GROUPS
from src.petkit.planning import template_sql

VERSION = 'metric-execution-v2'
RULES = {
    'net_sales': dict(unit='CNY', time_column='fact_order.paid_date',
        grain='order item; refunds aggregated by item_id before LEFT JOIN',
        formula='SUM(order_item.amount - COALESCE(refunds_by_item.refunded, 0))',
        rules=['退款截至快照日累计，归原支付月；不按退款发生月归属',
               '退款先按item_id求和再LEFT JOIN，禁止直接关联多条退款或SUM(DISTINCT amount)',
               '模板使用发布快照内全部订单；订单取消/支付状态的源纳入规则需Owner批准，不能自行添加过滤'],
        outputs=['net_sales']),
    'activation30': dict(unit='percent', time_column='fact_order.paid_date',
        grain='distinct sold device in mature cohort',
        formula='100 * distinct activated devices / distinct mature sold devices',
        rules=['支付日起0至30天（两端含）激活；只纳入观察满30天的售出设备',
               '分母保留无事件、晚激活和后续退款设备；按device_id去重',
               '事件窗口放在ON或独立outcomes CTE，不得在LEFT JOIN后WHERE缩小分母'],
        outputs=['devices', 'matched_devices', 'rate_percent']),
    'service30': dict(unit='percent', time_column='fact_order.paid_date',
        grain='distinct sold device in mature cohort',
        formula='100 * distinct devices with tickets / distinct mature sold devices',
        rules=['支付日起0至30天（两端含）有工单的设备数，不是工单条数',
               '只纳入观察满30天的售出设备，分母保留无工单、晚工单和后续退款设备',
               '事件先按设备去重，不得在LEFT JOIN后WHERE缩小分母'],
        outputs=['devices', 'matched_devices', 'rate_percent']),
    'offline_rate': dict(unit='percent', time_column='fact_device_day.event_date',
        grain='observed device day', formula='100 * SUM(CASE WHEN online=0 THEN 1 ELSE 0 END) / COUNT(*)',
        rules=['只统计采样设备日，不代表实时离线设备比例；不等于在线率',
               '输出百分数必须乘100；不得按设备去重后替代设备日分母'],
        outputs=['device_days', 'offline_percent']),
    'tickets': dict(unit='ticket', time_column='fact_service_ticket.created_date',
        grain='service ticket', formula='COUNT(*)',
        rules=['按创建日统计工单条数，不是设备数或售后率'], outputs=['tickets']),
    'received_units': dict(unit='piece', time_column='fact_receipt.receipt_date',
        grain='receipt fact', formula='SUM(fact_receipt.quantity)',
        rules=['按收货日汇总已接收件数；仅关联仓库维表',
               '不是订购量、库存余额或收货单条数；不得关联采购行重复放大数量'],
        outputs=['received_units']),
    'inventory': dict(unit='piece', time_column='fact_inventory_day.stock_date',
        grain='latest snapshot per (sku_id, warehouse_id)', formula='SUM(latest quantity)',
        rules=['每个SKU和仓库分别选范围内最新快照，再按型号汇总',
               '不能取全表统一MAX日期，不跨日期累加快照'], outputs=['stock_quantity']),
}


def sql_shape_contract(sql):
    """Logical compiler structure, never database results or physical mapping.

    The existing verifier enforces a specific shape rather than general SQL
    equivalence; make that constraint visible to generation and repair.
    """
    import sqlglot
    from sqlglot import exp
    tree=sqlglot.parse_one(sql,read='starrocks')
    def describe(node):
        def render(v):return v.sql(dialect='starrocks') if v is not None else None
        return dict(projections=[render(x) for x in node.expressions],
            source=render(node.args.get('from_')),
            joins=[render(x) for x in node.args.get('joins',[])],
            where=render(node.args.get('where')),group=render(node.args.get('group')),
            order=render(node.args.get('order')))
    return dict(verification='approved_normalized_shape_not_general_equivalence',
                outer=describe(tree),ctes=[dict(name=x.alias_or_name,**describe(x.this)) for x in tree.find_all(exp.CTE)])


def resolve(intent, catalog):
    """Produce a fresh, JSON-safe contract without credentials or reference SQL."""
    metric = next((m for m in catalog['metrics'] if m['id'] == intent.metric), None)
    if not metric or metric['version'] != intent.metric_version or catalog['as_of'] != intent.as_of:
        raise Clarify('指标或数据版本发生变化，请重新提问。', reason='metric_version')
    if intent.metric not in RULES or intent.group not in GROUPS[intent.metric]:
        raise Clarify('本指标或分组未登记执行规则。', reason='unsupported_metric')
    rule = RULES[intent.metric]
    dimension = {'model': 'product'}.get(intent.group, intent.group)
    if 'approved_dimensions' in metric and dimension not in metric['approved_dimensions']:
        raise Clarify('当前发布指标未批准该分组。', reason='dimension')
    if metric.get('unit', rule['unit']) != rule['unit']:
        raise Clarify('发布指标单位与执行规则不一致，需要Owner复核。', reason='metric_contract')
    dependencies = metric['dependencies']
    if set(dependencies) - catalog['tables'].keys():
        raise Clarify('指标依赖元数据不完整，暂不能计算。', reason='metric_contract')
    period = dict(kind='published_snapshot', start=None, end_exclusive=None,
                  as_of=intent.as_of, assumption='未指定月份时使用发布快照内全部观察记录，非默认本月')
    if intent.month:
        y, m = map(int, intent.month.split('-'))
        start = date(y, m, 1)
        end = date(y + (m == 12), m % 12 + 1, 1)
        if start > date.fromisoformat(intent.as_of):
            raise Clarify('请求月份晚于当前数据截止日，请修改月份。', reason='time_range')
        period = dict(kind='calendar_month', start=start.isoformat(), end_exclusive=end.isoformat(),
                      as_of=intent.as_of, partial_month=end > date.fromisoformat(intent.as_of) + timedelta(days=1))
    result = dict(schema_version=VERSION, metric=intent.metric, metric_version=metric['version'],
        definition=metric['definition'], unit=rule['unit'], time_column=rule['time_column'],
        time_range=period, grain=rule['grain'], formula=rule['formula'], rules=list(rule['rules']),
        group=intent.group, dependencies=list(dependencies), output_metrics=list(rule['outputs']),
        zero_denominator='NULL' if rule['unit']=='percent' else 'not_applicable', empty_result='empty rows; never replace errors with zero',
        business_approval_status=metric.get('approval_status', 'technical_contract_only'))
    result['sql_shape']=sql_shape_contract(template_sql(intent.metric,intent.group,intent.month,intent.as_of))
    if intent.metric in {'activation30', 'service30'}:
        result['maturity_cutoff'] = (date.fromisoformat(intent.as_of) - timedelta(days=30)).isoformat()
    # Bind to schema/approved edges/compiler SQL as well as human-readable rules.
    pin = dict(contract=result, tables={n:catalog['tables'][n] for n in dependencies},
        relationships=catalog['relationships'], compiler_sql=template_sql(intent.metric, intent.group, intent.month, intent.as_of))
    result['sha256'] = hashlib.sha256(json.dumps(pin, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result
