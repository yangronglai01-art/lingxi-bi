"""Bounded server-derived query intent and conservative approved SQL-shape gate.

Unknown filters/time definitions clarify. Normalized shape equality is not a
general relational equivalence proof; unfamiliar but correct SQL may be refused.
"""
from dataclasses import asdict, dataclass
from datetime import date
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer import optimize
from sqlglot.optimizer.scope import traverse_scope

from src.petkit.planning import examples, normalize, plan, template_sql


class Clarify(ValueError):
    def __init__(self,message,*,reason='intent',details=None):
        super().__init__(message);self.reason=reason;self.details=details


@dataclass(frozen=True)
class Intent:
    metric: str
    group: str
    month: str | None
    as_of: str
    metric_version: str

    def payload(self): return asdict(self)


GROUPS = {
    'net_sales': {'model', 'channel', 'month'},
    'tickets': {'model', 'fault'}, 'inventory': {'model'},
    'received_units': {'warehouse'}, 'activation30': {'model'},
    'service30': {'model'}, 'offline_rate': {'model', 'firmware'},
}
WORDS = {'model': ('型号', '产品'), 'channel': ('渠道',), 'month': ('每月', '按月', '月度趋势'),
         'fault': ('故障分类', '故障类型'), 'warehouse': ('仓库',), 'firmware': ('固件版本', '固件')}
NAMES = {'net_sales':'净销售额', 'tickets':'工单数', 'inventory':'期末库存',
         'received_units':'采购收货数量', 'activation30':'售出30天激活率',
         'service30':'售出30天售后设备率', 'offline_rate':'设备离线率'}
PREFIX = {'model':'各型号', 'channel':'各渠道', 'month':'每月', 'fault':'各故障分类', 'warehouse':'各仓库', 'firmware':'各固件版本'}


def preflight(question):
    """Only expose choices; never silently choose a financial definition."""
    text = question.replace('净销售额', '')
    if any(word in text for word in ('销售额', '收入', '销售趋势')):
        raise Clarify('请确认需要订单实付金额，还是扣除退款后的净销售额？', reason='metric_ambiguity',
                      details={'missing_slots':['metric_definition'], 'options':['订单实付金额（需登记）','净销售额']})
    if '在线率' in question:
        raise Clarify('在线率与离线率不同；请确认指标及设备日或实时设备口径。', reason='metric_ambiguity',
                      details={'missing_slots':['metric_definition','denominator'], 'options':['采样设备日离线率','在线率（需登记）']})
    if any(word in question for word in ('退款率', '退款比例')):
        raise Clarify('请确认按退款金额、订单数还是设备数计算，以及退款截止日和归属月份；当前尚未批准退款率执行规则。',
                      reason='unsupported_metric', details={'missing_slots':['numerator','denominator','time_attribution'],
                      'options':['退款金额/实付金额','退款订单数/支付订单数'], 'requires_owner_approval':True})
    if any(word in question for word in ('同比','环比')):
        raise Clarify('请明确指标、当前周期、比较周期及同期是否按相同截止日对齐；当前比较规则尚未批准。',
                      reason='comparison', details={'missing_slots':['metric','current_period','comparison_period','alignment'], 'requires_owner_approval':True})
    if any(word in question for word in ('累计','分层')):
        raise Clarify('请明确累计起点/终点、重置周期，或分层字段和边界；当前复杂计算规则尚未批准。',
                      reason='complex_definition', details={'missing_slots':['time_range','calculation_boundaries'], 'requires_owner_approval':True})


def derive(question, catalog):
    as_of = catalog['as_of']; date.fromisoformat(as_of)
    preflight(question)
    proposal = plan(question, as_of=as_of)
    if proposal['status'] != 'ready' or not proposal.get('metric'):
        raise Clarify(proposal.get('message') or '请明确一个已登记指标及统计范围。',
                      reason='metric_definition', details={'missing_slots':['metric_definition']})
    metric = next((m for m in catalog['metrics'] if m['id'] == proposal['metric']['id']), None)
    if not metric or metric['id'] not in GROUPS: raise Clarify('本指标没有批准的查询结构。')
    approved = next((x for x in examples(as_of) if normalize(x['question']) == normalize(question)), None)
    if approved: return Intent(metric['id'], approved['group'], approved['month'], as_of, metric['version'])
    if re.search(r'同比|环比|累计|分层|仅|只看|筛选|大于|小于|排名|前\d|TOP|华东|华南|地区|区域|今年|去年|上月|本月|最近|近\d|\d{4}-\d{2}-\d{2}', question, re.I):
        raise Clarify('当前尚未批准此过滤或相对时间口径，请明确筛选值、分组和年月。',
                      reason='unsupported_filter', details={'missing_slots':['filters','calendar_month']})
    months = re.findall(r'(\d{4})年(\d{1,2})月', question)
    if len(months) > 1: raise Clarify('一次仅支持一个明确月份；比较周期需要批准口径。')
    month = None
    if months:
        year, num = map(int, months[0])
        try: month = date(year, num, 1).strftime('%Y-%m')
        except ValueError: raise Clarify('月份无效。') from None
    groups = [g for g, words in WORDS.items() if g in GROUPS[metric['id']] and any(w in question for w in words)]
    if len(groups) != 1:
        raise Clarify('请明确单一分组，例如各型号、各渠道或每月。', reason='dimension',
                      details={'missing_slots':['dimension'], 'options':sorted(GROUPS[metric['id']])})
    remainder=re.sub(r'\d{4}年\d{1,2}月','',question)
    words=[metric['name'],*metric.get('aliases',[]),*WORDS[groups[0]],'请','查询','统计','展示','查看','看看','帮我','给我','各','按','的','情况','是多少','多少','呢','趋势','谢谢']
    remainder=re.sub('|'.join(re.escape(w) for w in sorted(set(words),key=len,reverse=True)),'',remainder)
    if re.sub(r'[\s？?，,。！!：:；;]','',remainder):raise Clarify('问题含未登记的条件，请明确批准的分组、年月和口径。')
    return Intent(metric['id'], groups[0], month, as_of, metric['version'])


def approved_intent_example(intent, catalog):
    """Only reuse a registered compiler plan with the exact parsed signature.

    Call after derive has rejected unknown filters, relative times and ambiguity.
    Matching a metric name alone is never sufficient.
    """
    matches=[e for e in examples(intent.as_of)
             if (e['metric_id'],e['group'],e['month']) ==
                (intent.metric,intent.group,intent.month)]
    if len(matches)!=1:return None
    return matches[0]


def canonical_question(intent):
    prefix=''
    if intent.month:
        year,month=map(int,intent.month.split('-'));prefix=f'{year}年{month}月'
    suffix='趋势' if intent.group=='month' else ''
    return prefix + PREFIX[intent.group] + NAMES[intent.metric] + suffix


def followup(question, previous, catalog):
    """Only explicit group/month patches. No SQL/history string concatenation."""
    intent = Intent(**previous)
    metric = next((m for m in catalog['metrics'] if m['id'] == intent.metric), None)
    if not metric or metric['version'] != intent.metric_version or intent.as_of != catalog['as_of']:
        raise Clarify('指标或数据版本已经改变，请重新提问。')
    if any(name in question for name in NAMES.values()): return canonical_question(derive(question, catalog))
    text = normalize(question)
    month_match = re.fullmatch(r'(?:改为|换成|那|看|改看)?(\d{4})年(\d{1,2})月(?:呢)?', text)
    if month_match:
        try: month = date(*map(int, month_match.groups()), 1).strftime('%Y-%m')
        except ValueError: raise Clarify('月份无效。') from None
        return canonical_question(Intent(intent.metric, intent.group, month, intent.as_of, intent.metric_version))
    accepted = {'按型号看':'model','按产品看':'model','按渠道看':'channel','按月看':'month',
                '按仓库看':'warehouse','按故障分类看':'fault','按固件看':'firmware'}
    group = accepted.get(text)
    if group not in GROUPS[intent.metric]: raise Clarify('追问仅支持明确切换已批准分组或年月；其他条件请完整提问。')
    return canonical_question(Intent(intent.metric, group, intent.month, intent.as_of, intent.metric_version))


def shape(sql, catalog):
    tree = sqlglot.parse_one(sql, read='starrocks')
    if not isinstance(tree, exp.Select): raise Clarify('仅支持批准的SELECT结构。')
    tree.set('limit', None)  # outer response cap only; inner LIMIT remains semantic.
    # Canonicalize CTE and source aliases, preserving scopes/correlated references.
    ctes = list(tree.find_all(exp.CTE)); cte_names = {c.alias_or_name: f'cte_{i}' for i,c in enumerate(ctes)}
    if len(cte_names) != len(ctes): raise Clarify('重复CTE名称需明确。')
    for t in tree.find_all(exp.Table):
        if t.name in cte_names: t.set('this', exp.to_identifier(cte_names[t.name]))
    for c in ctes: c.set('alias', exp.TableAlias(this=exp.to_identifier(cte_names[c.alias_or_name])))
    scopes = list(traverse_scope(tree))
    maps = {id(s): {alias: f'source_{i}' for i,alias in enumerate(s.sources)} for s in scopes}
    changes = []
    for s in scopes:
        for col in s.columns:
            owner=s
            while owner and col.table not in maps[id(owner)]: owner=owner.parent
            if owner: changes.append((col, maps[id(owner)][col.table]))
    for col, alias in changes: col.set('table', exp.to_identifier(alias))
    for s in scopes:
        for alias, source in s.selected_sources.items():
            node=source[0]
            # Derived sources expose their SELECT in selected_sources; the
            # binding alias belongs to the enclosing Subquery, not the SELECT.
            if isinstance(node, exp.Select) and isinstance(node.parent, exp.Subquery):
                node=node.parent
            node.set('alias', exp.TableAlias(this=exp.to_identifier(maps[id(s)][alias])))
    schema={n:{c:x['type'] for c,x in t['columns'].items()} for n,t in catalog['tables'].items()}
    return optimize(tree, schema=schema, dialect='starrocks').sql(dialect='starrocks')


def verify_sql(sql, intent, catalog, max_rows=1000):
    expected = template_sql(intent.metric, intent.group, intent.month, intent.as_of)
    try:
        from src.petkit.guard import check
        tree=sqlglot.parse_one(sql,read='starrocks');limit=tree.args.get('limit')
        if tree.args.get('offset') or (limit and (not isinstance(limit.expression,exp.Literal) or limit.expression.is_string or limit.expression.this!=str(max_rows+1))):
            raise Clarify('SQL擅自改变结果范围或分页，请明确查询范围。',reason='semantic')
        context=dict(tables=catalog['tables'],relationships=catalog['relationships'])
        actual=check(sql,context,max_rows).sql
        reference=check(expected,context,max_rows).sql
        actual_shape=shape(actual,catalog)
        if actual_shape != shape(reference,catalog):
            from .cohort_equivalence import reference_alternatives
            accepted=next((label for label,candidate in reference_alternatives(intent,catalog,expected)
                           if actual_shape==shape(check(candidate,context,max_rows).sql,catalog)),None)
            if accepted is None:
                raise Clarify('SQL与批准指标的粒度、时间、过滤或关联结构不一致，请澄清。',reason='semantic')
            return dict(method='approved_normalized_shape',general_equivalence_proof=False,limited_equivalence=accepted)
    except Clarify: raise
    except (sqlglot.errors.ParseError, sqlglot.errors.OptimizeError):
        # Binding/normalization errors can be corrected within the same pinned
        # contract. PermissionError and infrastructure failures remain terminal.
        raise
    except Exception: raise Clarify('无法确定SQL是否符合批准口径，请澄清。') from None
    return dict(method='approved_normalized_shape', general_equivalence_proof=False)
