"""Finite compiler-owned alternatives; never a general SQL equivalence proof."""
import sqlglot
from sqlglot import exp


def reference_alternatives(intent, catalog, reference):
    if intent.metric not in {'activation30', 'service30'}:
        return []
    event, field = (('fact_device_activation', 'activation_date')
                    if intent.metric == 'activation30' else ('fact_service_ticket', 'created_date'))
    # No date/timestamp/timezone coercion assumptions outside this DATE schema.
    for table, column in [('fact_order', 'paid_date'), (event, field)]:
        if catalog['tables'].get(table, {}).get('columns', {}).get(column, {}).get('type', '').upper() != 'DATE':
            return []
    tree = sqlglot.parse_one(reference, read='starrocks')
    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) != 2:
        return []
    intervals = list(tree.find_all(exp.Interval))
    if len(intervals) != 2 or any(x.this.this != '30' or x.text('unit').upper() not in {'DAY', 'DAYS'} for x in intervals):
        return []
    # SQLGlot's simplification differs for DAY vs DAYS. Normalize the trusted
    # compiler reference, not arbitrary user expressions or timestamp casts.
    for interval in intervals:
        interval.set('unit', exp.Var(this='DAY'))
    result = [('date_day_unit', tree.sql(dialect='starrocks'))]

    # DISTINCT in the one-key outcomes relation is redundant only for this
    # exact compiler-owned consumer: LEFT JOIN on the same key and exclusively
    # COUNT(DISTINCT key) aggregates. Do not remove it in arbitrary model SQL.
    outcomes = ctes[1]
    projection = outcomes.this.expressions
    if len(projection) != 1 or not isinstance(projection[0], exp.Column) or projection[0].name != 'device_id':
        return result
    joins = [j for j in tree.args.get('joins', []) if isinstance(j.this, exp.Table) and j.this.name == outcomes.alias_or_name]
    if len(joins) != 1 or joins[0].side.upper() != 'LEFT':
        return result
    event_alias = joins[0].this.alias_or_name
    # CTE reference count includes its declaration? exp.Table counts uses only.
    if sum(t.name == outcomes.alias_or_name for t in tree.find_all(exp.Table)) != 1:
        return result
    for column in tree.find_all(exp.Column):
        if column.find_ancestor(exp.Select) is not tree or column.table != event_alias:
            continue
        count = column.find_ancestor(exp.Count)
        on = joins[0].args.get('on')
        if on is not None and column in list(on.find_all(exp.Column)):
            continue
        if column.name != 'device_id' or count is None or not isinstance(count.this, exp.Distinct):
            return result
    aggregates = [a for a in tree.find_all(exp.AggFunc) if a.find_ancestor(exp.Select) is tree]
    if not aggregates or any(not isinstance(a, exp.Count) or not isinstance(a.this, exp.Distinct) for a in aggregates):
        return result
    if tree.args.get('having') or tree.args.get('qualify') or list(tree.find_all(exp.Window)):
        return result
    outcomes.this.set('distinct', None)
    result.append(('date_day_unit_and_redundant_outcome_distinct', tree.sql(dialect='starrocks')))
    return result
