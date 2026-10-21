"""Fail-closed AST allowlist. It is not a substitute for production DB authorization."""
from dataclasses import dataclass


@dataclass
class Checked:
    sql: str
    tables: list


FUNCTIONS={"SUM","AVG","COUNT","MIN","MAX","ROUND","ABS","COALESCE","NULLIF",
 "CAST","TRY_CAST","TIME_TO_STR","STRFTIME","DATE_TRUNC","TIMESTAMP_TRUNC","EXTRACT",
 "YEAR","MONTH","DAY","LOWER","UPPER","LENGTH","CONCAT","SUBSTRING","TRIM",
 "ROW_NUMBER","RANK","DENSE_RANK","LAG","LEAD","CASE","IF","CURRENT_DATE",
 "DATE_DIFF","DATEDIFF","DATE_ADD","DATE_SUB","TS_OR_DS_TO_DATE","TIME_STR_TO_TIME"}


def check(sql,context,max_rows=1000):
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import traverse_scope
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    parsed=sqlglot.parse(sql,read="duckdb")
    statements=[s for s in parsed if s is not None]
    if len(statements)!=1 or not isinstance(statements[0],exp.Select):
        raise PermissionError("只允许单条SELECT，可包含非递归CTE")
    tree=normalize_identifiers(statements[0],dialect="duckdb")
    for node in tree.walk():
        if node.key in {"insert","update","delete","create","drop","alter","copy","command",
                        "into","lock","transaction","pragma","attach","detach"}:
            raise PermissionError("禁止写入、管理或外部访问")
        if isinstance(node,exp.With) and node.args.get("recursive"):
            raise PermissionError("禁止递归查询")
        # SQLGlot also models boolean connectors as Func; they are syntax,
        # not user-callable functions. ON's OR remains explicitly forbidden below.
        if isinstance(node,exp.Func) and not isinstance(node,(exp.And,exp.Or)):
            name=node.name.upper() if isinstance(node,exp.Anonymous) else node.sql_name().upper()
            if name not in FUNCTIONS: raise PermissionError(f"函数未获批准：{name}")
    physical=set()
    for scope in traverse_scope(tree):
        for alias,source in scope.sources.items():
            if isinstance(source,exp.Table):
                if not isinstance(source.this,exp.Identifier) or source.db or source.catalog:
                    raise PermissionError("禁止表函数或外部库路径")
                name=source.name.lower()
                if name not in context["tables"]: raise PermissionError(f"表不在本次授权检索上下文中：{name}")
                physical.add(name)
        for join in scope.expression.find_all(exp.Join):
            # Descendant scopes will be validated separately.
            if join.find_ancestor(exp.Select) is not scope.expression: continue
            on=join.args.get("on")
            if join.args.get("kind","").upper()=="CROSS" or on is None:
                raise PermissionError("禁止无ON关联和笛卡尔积")
            if any(True for _ in on.find_all(exp.Or)):
                raise PermissionError("ON中不允许OR，避免弱化关联约束")
            joined_alias=join.this.alias_or_name
            approved=False
            for equality in on.find_all(exp.EQ):
                a,b=equality.left,equality.right
                if not (isinstance(a,exp.Column) and isinstance(b,exp.Column) and a.table and b.table and a.table!=b.table): continue
                if joined_alias not in {a.table,b.table}: continue
                left,right=scope.sources.get(a.table),scope.sources.get(b.table)
                if left is None or right is None:
                    raise PermissionError("关联引用了未知数据源别名")
                if isinstance(left,exp.Table) and isinstance(right,exp.Table):
                    pair={(left.name.lower(),a.name.lower()),(right.name.lower(),b.name.lower())}
                    if not any(pair=={(r["left"],r["left_key"]),(r["right"],r["right_key"])} for r in context["relationships"]):
                        raise PermissionError("直接表关联键不在批准关系中")
                # CTE/derived joins still undergo column binding but cardinality needs semantic checks.
                approved=True
            if not approved: raise PermissionError("关联条件必须包含不同数据源列之间的等值条件")
    if not physical: raise PermissionError("查询必须引用批准的业务表")
    schema={n:{c:v["type"] for c,v in t["columns"].items()} for n,t in context["tables"].items()}
    tree=qualify(tree,dialect="duckdb",schema=schema,validate_qualify_columns=True,
                 quote_identifiers=True,identify=True)
    if len(list(tree.find_all(exp.Join)))>12: raise PermissionError("关联数量超过12个")
    # Outer limit is replaced as a unit. Nested limits cannot weaken it.
    old=tree.args.get("limit")
    requested=max_rows+1
    if old is not None:
        value=old.expression
        if not isinstance(value,exp.Literal) or not value.is_int: raise PermissionError("LIMIT必须为非负整数")
        requested=min(requested,int(value.this))
        if requested<0: raise PermissionError("LIMIT不能为负数")
    tree=tree.limit(requested)
    return Checked(tree.sql(dialect="duckdb"),sorted(physical))
