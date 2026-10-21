"""Small deterministic metric gate; not a complete SQL equivalence prover."""
from datetime import date,timedelta


class MetricError(ValueError):
    pass


def _date_literal(node):
    from sqlglot import exp
    if isinstance(node,(exp.Sub,exp.Add)) and isinstance(node.right,exp.Interval):
        base=_date_literal(node.left)
        interval=node.right
        if base and interval.text('unit').upper() in {'DAY','DAYS'}:
            try: days=int(interval.this.this)
            except (ValueError,TypeError): return None
            return base+timedelta(days=days if isinstance(node,exp.Add) else -days)
    if isinstance(node,exp.Cast): node=node.this
    if isinstance(node,exp.Literal) and node.is_string:
        try: return date.fromisoformat(node.this)
        except ValueError: return None
    return None


def _conjuncts(node):
    from sqlglot import exp
    while isinstance(node,exp.Paren):node=node.this
    if isinstance(node,exp.And):
        yield from _conjuncts(node.left)
        yield from _conjuncts(node.right)
    else: yield node


def _column_metadata(scope,column,context,depth=0):
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope
    if depth>12 or not isinstance(column,exp.Column): return None
    source=scope.sources.get(column.table)
    if source is None and scope.parent is not None:
        return _column_metadata(scope.parent,column,context,depth+1)
    if isinstance(source,exp.Table):
        return context["tables"].get(source.name,{}).get("columns",{}).get(column.name)
    if isinstance(source,Scope):
        for projection in source.expression.selects:
            if projection.alias_or_name==column.name:
                value=projection.this if isinstance(projection,exp.Alias) else projection
                return _column_metadata(source,value,context,depth+1)
    return None


def _validate_enums(tree,context):
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    for scope in traverse_scope(tree):
        for term in scope.expression.walk():
            if term.find_ancestor(exp.Select) is not scope.expression: continue
            pairs=[]
            if isinstance(term,(exp.EQ,exp.NEQ)):
                pairs=[(term.left,term.right),(term.right,term.left)]
            elif isinstance(term,exp.In): pairs=[(term.this,value) for value in term.expressions]
            for col,value in pairs:
                metadata=_column_metadata(scope,col,context)
                if not metadata or "enum" not in metadata: continue
                if isinstance(value,exp.Literal) and value.is_string and value.this not in metadata["enum"]:
                    raise MetricError(f"字段{col.sql()}的值{value.this!r}不在当前完整枚举{metadata['enum']}中；不能翻译或臆造数据库状态值。请使用元数据给出的实际值或澄清。")


def _origin(scope,column,depth=0):
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope
    if depth>12 or not isinstance(column,exp.Column): return None
    source=scope.sources.get(column.table)
    nullable=any(j.side.upper()=='LEFT' and j.this.alias_or_name==column.table
                 for j in scope.expression.args.get('joins',[]))
    if isinstance(source,exp.Table): return source.name,column.name,nullable
    if isinstance(source,Scope):
        for projection in source.expression.selects:
            if projection.alias_or_name==column.name:
                value=projection.this if isinstance(projection,exp.Alias) else projection
                origin=_origin(source,value,depth+1)
                if origin: return origin[0],origin[1],nullable or origin[2]
    return None


def _preserve_cohort(tree,metric):
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    event_table='fact_device_activation' if metric=='activation30' else 'fact_service_ticket'
    event_column='activation_date' if metric=='activation30' else 'created_date'
    for scope in traverse_scope(tree):
        where=scope.expression.args.get('where')
        if where is None: continue
        for column in where.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not scope.expression: continue
            origin=_origin(scope,column)
            if origin and origin[:2]==(event_table,event_column) and origin[2]:
                raise MetricError('30天指标分母不得被LEFT JOIN事件日期的WHERE条件缩小。将事件窗口放进LEFT JOIN的ON，或在独立outcomes CTE筛选分子，再LEFT JOIN完整cohort；保留晚激活/晚报修和无事件设备。')


def _validate_event_window(tree,metric):
    """Fail closed for aggregate cohorts outside the supported local window shapes.

    Each direct event scan must bound its event date by the related paid date
    in that SELECT's WHERE or the ON that introduces the event on a
    non-preserved join side. A later outer join's ON cannot filter event rows
    already preserved on its left side. Raw event CTEs filtered by calendar cutoff
    are deliberately unsupported, even if a later query could filter them.
    This is a bounded shape validator, not a general SQL equivalence proof.
    """
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    if not any(tree.find_all(exp.AggFunc)):return
    event_table,event_column=(('fact_device_activation','activation_date') if metric=='activation30'
                              else ('fact_service_ticket','created_date'))
    def unwrap(node):
        while isinstance(node,(exp.Paren,exp.Cast)):node=node.this
        return node
    def paid(scope,node):
        origin=_origin(scope,unwrap(node))
        return origin is not None and origin[:2]==('fact_order','paid_date')
    def upper(scope,node,lower):
        node=unwrap(node)
        if not isinstance(node,exp.Add):return False
        interval=node.right
        return (isinstance(interval,exp.Interval) and interval.text('unit').upper() in {'DAY','DAYS'}
                and interval.this.this=='30' and paid(scope,node.left)
                and unwrap(node.left).sql()==unwrap(lower).sql())
    for scope in traverse_scope(tree):
        aliases=[alias for alias,source in scope.sources.items()
                 if isinstance(source,exp.Table) and source.name==event_table]
        if not aliases:continue
        where=scope.expression.args.get('where')
        for alias in aliases:
            roots=[where.this] if where else []
            for join in scope.expression.args.get('joins',[]):
                # Only the event's own INNER/LEFT JOIN can bound its matches.
                # RIGHT/FULL JOIN preserves unmatched events even when ON fails.
                if (join.this.alias_or_name==alias and join.side.upper() in {'','LEFT'}
                        and join.kind.upper() in {'','INNER'} and join.args.get('on') is not None):
                    roots.append(join.args['on'])
            terms=[term for root in roots for term in _conjuncts(unwrap(root))]
            def event(node):
                node=unwrap(node)
                return isinstance(node,exp.Column) and node.table==alias and node.name==event_column
            valid=False
            lowers=[];uppers=[]
            for term in terms:
                term=unwrap(term)
                if isinstance(term,exp.Between) and event(term.this):
                    low=term.args['low'];high=term.args['high']
                    valid=valid or (paid(scope,low) and upper(scope,high,low))
                if isinstance(term,exp.GTE):
                    if event(term.left) and paid(scope,term.right):lowers.append(term.right)
                    if event(term.right):uppers.append(term.left)
                if isinstance(term,exp.LTE):
                    if event(term.left):uppers.append(term.right)
                    if event(term.right) and paid(scope,term.left):lowers.append(term.left)
            valid=valid or any(upper(scope,high,low) for low in lowers for high in uppers)
            if not valid:
                raise MetricError('30天指标分子缺少逐设备支付后0至30天事件窗口。保留cohort.paid_date，在直接读取事件表的outcomes CTE中使用 WHERE e.'+event_column+' BETWEEN c.paid_date AND c.paid_date + INTERVAL \'30 days\'，然后LEFT JOIN完整cohort；只限制事件日期小于数据截止日不等价。')


def validate_metric(sql,context):
    metric=context.get('metric') or {}
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    tree=sqlglot.parse_one(sql,read='duckdb')
    _validate_enums(tree,context)
    if metric.get('id')=='received_units':
        tables=list(tree.find_all(exp.Table));selects=list(tree.find_all(exp.Select))
        if len(selects)!=1 or len(tables)!=2 or {t.name for t in tables}!={'fact_receipt','dim_warehouse'}:
            raise MetricError('收货数量使用收货事实与仓库维表直接聚合，禁止关联采购明细、库存或重复收货表造成倍增')
        aliases={t.alias_or_name:t.name for t in tables}
        aggregates=list(tree.find_all(exp.AggFunc))
        if len(aggregates)!=1 or not isinstance(aggregates[0],exp.Sum): raise MetricError('收货数量必须SUM(quantity)，不能用收货单数COUNT或其他聚合代替')
        value=aggregates[0].this
        if not isinstance(value,exp.Column) or value.name!='quantity' or aliases.get(value.table)!='fact_receipt': raise MetricError('收货数量必须直接求和收货事实quantity，不能用订购量、金额或DISTINCT去重数量')
        return
    if metric.get('id') not in {'activation30','service30'}: return
    _preserve_cohort(tree,metric['id'])
    _validate_event_window(tree,metric['id'])
    cutoff=date.fromisoformat(context['data_as_of'])-timedelta(days=30)
    observed=False
    for scope in traverse_scope(tree):
        aliases=[alias for alias,source in scope.sources.items()
                 if isinstance(source,exp.Table) and source.name.lower()=='fact_order']
        if not aliases: continue
        observed=True
        where=scope.expression.args.get('where')
        terms=list(_conjuncts(where.this)) if where else []
        for alias in aliases:
            safe=False
            for term in terms:
                if not isinstance(term,(exp.LT,exp.LTE)): continue
                col=term.left
                if not isinstance(col,exp.Column) or col.table!=alias or col.name!='paid_date': continue
                bound=_date_literal(term.right)
                if bound is not None and ((isinstance(term,exp.LTE) and bound<=cutoff)
                                           or (isinstance(term,exp.LT) and bound<=cutoff+timedelta(days=1))):
                    safe=True
            if not safe:
                raise MetricError(f"30天指标缺少成熟观察窗：对{alias}.paid_date添加AND {alias}.paid_date <= DATE '{cutoff.isoformat()}'，并在每个独立订单CTE中执行此约束。")
    if not observed:
        raise MetricError('30天指标必须通过fact_order支付日确定售出设备和成熟观察窗')
