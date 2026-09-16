# -*- coding: utf-8 -*-
"""SQL 安全校验模块（sql_guard）。

在 SQL 执行前对 LLM 生成的查询做严格白名单校验，防止注入与越权访问。拦截规则：
1. 仅允许 SELECT（可含 WITH CTE）；
2. 拒绝多语句（分号分隔）；
3. 拒绝任何写入 / DDL / 管理语句（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA/ATTACH …）；
4. 表 / 字段白名单：只能访问 src.schema.TABLES 声明的表与列；
5. 强制结果行数上限（默认 config.MAX_RESULT_ROWS，缺失则注入、超限则收紧）。

实现采用「自研词法分析器 + 白名单」策略，不依赖 sqlparse/sqlglot，行为确定、
可离线运行、可单测。解析失败或任一校验不通过即拒绝执行并返回中文原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from src import config, schema

# ---------------------------------------------------------------------------
# 常量（统一小写比较，避免大小写差异）
# ---------------------------------------------------------------------------
# 禁止出现的语句关键字（一旦作为关键字出现即整体拒绝）
FORBIDDEN_KEYWORDS: Set[str] = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "merge", "replace", "pragma", "attach", "detach", "copy", "export",
    "import", "call", "set", "grant", "revoke", "begin", "commit",
    "rollback", "vacuum", "use", "execute", "explain", "analyze",
    "reindex", "install", "load",
}

# 允许的语句起始关键字（WITH 用于支持 CTE）
_ALLOWED_STARTERS: Set[str] = {"select", "with"}

# SQL 语法关键字（用于区分「字段名」与「语法词」）
_KEYWORDS: Set[str] = {
    "select", "from", "where", "group", "by", "order", "having", "limit",
    "offset", "as", "and", "or", "not", "in", "between", "like", "ilike",
    "is", "null", "case", "when", "then", "else", "end", "distinct", "all",
    "asc", "desc", "on", "join", "inner", "left", "right", "full", "outer",
    "cross", "union", "with", "over", "partition", "true", "false",
    "cast", "extract", "interval", "filter", "using", "natural", "exists",
    "except", "intersect",
}

# 允许出现的函数名（聚合 / 标量）。其余裸标识符会被当作字段名做白名单校验。
_FUNCTIONS: Set[str] = {
    "sum", "avg", "count", "min", "max", "round", "abs", "ceil", "ceiling",
    "floor", "sqrt", "power", "lag", "lead", "first_value", "last_value",
    "row_number", "rank", "dense_rank", "coalesce", "ifnull", "nullif",
    "lower", "upper", "length", "concat", "substr", "substring", "trim",
    "replace", "strftime", "date_trunc", "date", "year", "month", "day",
    "hour", "current_date", "current_timestamp", "now", "string_agg", "list",
}

# 预计算表/字段白名单（基于 schema.TABLES，均小写）
TABLE_COLUMNS: Dict[str, Set[str]] = {
    name: {c.name.lower() for c in table.columns}
    for name, table in schema.TABLES.items()
}
ALL_COLUMNS: Set[str] = {col for cols in TABLE_COLUMNS.values() for col in cols}


class GuardError(Exception):
    """词法解析失败等致命错误（同样视为不安全，拒绝执行）。"""


@dataclass
class Token:
    """一个词法单元：类型、取值、在原 SQL 中的起止偏移（用于 LIMIT 精确改写）。"""

    kind: str
    value: str
    start: int = 0
    end: int = 0


@dataclass
class GuardResult:
    """校验结果：ok 为 True 时 sql 为注入/收紧 LIMIT 后的最终可执行语句。"""

    ok: bool
    sql: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# 词法分析
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
      (?P<COMMENT>--[^\n]*|/\*.*?\*/)
    | (?P<STRING>'(?:[^']|'')*')
    | (?P<DQIDENT>"(?:[^"]|"")*")
    | (?P<NUMBER>\b\d+(?:\.\d+)?\b)
    | (?P<STAR>\*)
    | (?P<COMMA>,)
    | (?P<SEMI>;)
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    | (?P<OP><=|>=|<>|!=|=|<|>|\+|-|/|%|\|\||::)
    | (?P<DOT>\.)
    | (?P<WORD>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<WS>\s+)
    """,
    re.VERBOSE | re.DOTALL,
)


def tokenize(sql: str) -> List[Token]:
    """把 SQL 切分为 token 列表（跳过空白与注释），记录每个 token 的偏移。"""
    tokens: List[Token] = []
    pos = 0
    n = len(sql)
    while pos < n:
        m = _TOKEN_RE.match(sql, pos)
        if not m:
            raise GuardError(f"无法解析的字符：{sql[pos]!r}（位置 {pos}）")
        pos = m.end()
        kind = m.lastgroup
        if kind == "WS":
            continue
        tokens.append(Token(kind, m.group(), m.start(), m.end()))
    return tokens


# ---------------------------------------------------------------------------
# 校验主入口
# ---------------------------------------------------------------------------
def check(sql: str, max_rows: Optional[int] = None) -> GuardResult:
    """对 SQL 做安全校验；通过则返回注入/收紧 LIMIT 后的最终 SQL。

    参数:
        sql: 待校验 SQL。
        max_rows: 结果行数上限，默认 config.MAX_RESULT_ROWS。

    返回:
        GuardResult（ok=True 时 sql 为最终可执行语句）。
    """
    limit = max_rows if max_rows is not None else config.MAX_RESULT_ROWS
    sql = (sql or "").strip()
    if not sql:
        return GuardResult(False, reason="SQL 为空")

    try:
        tokens = tokenize(sql)
    except GuardError as exc:
        return GuardResult(False, reason=str(exc))

    # 1) 拒绝多语句
    stmts, err = _split_statements(tokens)
    if err:
        return GuardResult(False, reason=err)
    if len(stmts) != 1:
        return GuardResult(False, reason="仅允许单条 SELECT 语句，禁止分号分隔的多语句")
    body = stmts[0]

    # 2) 起始关键字校验
    first = _first_word(body)
    if first not in _ALLOWED_STARTERS:
        return GuardResult(False, reason=f"仅允许 SELECT 查询，禁止以 {first.upper()} 开头")

    # 3) 禁止关键字校验
    for t in body:
        if t.kind == "WORD" and t.value.lower() in FORBIDDEN_KEYWORDS:
            return GuardResult(False, reason=f"禁止使用 {t.value.upper()} 语句")

    # 4) 表 / 字段白名单校验
    try:
        cte_names = _collect_cte_names(body)
        tables = _extract_tables(body, cte_names)
        col_err = _validate_columns(body, tables, cte_names)
    except GuardError as exc:
        return GuardResult(False, reason=str(exc))
    if col_err:
        return GuardResult(False, reason=col_err)

    # 5) LIMIT 注入 / 收紧
    return GuardResult(True, _enforce_limit(sql, limit))


# ---------------------------------------------------------------------------
# 各校验步骤实现
# ---------------------------------------------------------------------------
def _split_statements(tokens: List[Token]):
    """按顶层分号切分语句；返回 (语句列表, 错误信息)。"""
    stmts: List[List[Token]] = []
    cur: List[Token] = []
    depth = 0
    for t in tokens:
        if t.kind == "SEMI" and depth == 0:
            if cur:
                stmts.append(cur)
                cur = []
            # 忽略紧邻的空语句 / 重复分号
        else:
            if t.kind == "LPAREN":
                depth += 1
            elif t.kind == "RPAREN":
                depth = max(0, depth - 1)
            cur.append(t)
    if cur:
        stmts.append(cur)
    if len(stmts) > 1:
        return [], "仅允许单条 SELECT 语句，禁止分号分隔的多语句"
    return stmts, None


def _first_word(tokens: List[Token]) -> str:
    """取第一个 WORD token（小写）；无则返回空串。"""
    for t in tokens:
        if t.kind == "WORD":
            return t.value.lower()
    return ""


def _collect_cte_names(tokens: List[Token]) -> Set[str]:
    """收集 WITH 子句中的 CTE 名（支持 name AS (...) 与 name(col1,col2) AS (...)。"""
    names: Set[str] = set()
    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        if t.kind != "WORD":
            i += 1
            continue
        j = i + 1
        # 跳过可选的 (col1, col2) 列定义列表
        if j < n and tokens[j].kind == "LPAREN":
            depth = 1
            j += 1
            while j < n and depth > 0:
                if tokens[j].kind == "LPAREN":
                    depth += 1
                elif tokens[j].kind == "RPAREN":
                    depth -= 1
                j += 1
        # 期望其后是 AS，且 AS 后是 (
        if j < n and tokens[j].kind == "WORD" and tokens[j].value.lower() == "as":
            if j + 1 < n and tokens[j + 1].kind == "LPAREN":
                names.add(t.value.lower())
        i += 1
    return names


def _extract_tables(tokens: List[Token], cte_names: Set[str]) -> Dict[str, str]:
    """提取 FROM / JOIN 后的表名，返回 {别名(小写): 实际表名(小写)}。"""
    tables: Dict[str, str] = {}
    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        if t.kind == "WORD" and t.value.lower() in ("from", "join"):
            j = i + 1
            if j >= n:
                raise GuardError(f"{t.value.upper()} 后缺少表名")
            if tokens[j].kind == "LPAREN":
                raise GuardError("不支持子查询作为数据源")
            if tokens[j].kind != "WORD":
                raise GuardError(f"{t.value.upper()} 后不是合法的表名")
            name = tokens[j].value.lower()
            _check_table(name, cte_names)
            alias = _read_alias(tokens, j + 1)
            tables[(alias or name).lower()] = name
            i = j + 1
        else:
            i += 1
    return tables


def _check_table(name: str, cte_names: Set[str]) -> None:
    """表名必须在 schema 白名单或本语句 CTE 中。"""
    if name not in schema.TABLES and name not in cte_names:
        raise GuardError(f"不允许访问的表：{name}")


def _read_alias(tokens: List[Token], idx: int) -> Optional[str]:
    """读取表名后可选别名（支持 table alias 与 table AS alias 两种写法）。"""
    if idx >= len(tokens):
        return None
    t = tokens[idx]
    if t.kind == "WORD" and t.value.lower() == "as":
        if idx + 1 < len(tokens) and tokens[idx + 1].kind == "WORD":
            return tokens[idx + 1].value.lower()
        return None
    if t.kind == "WORD" and t.value.lower() not in _KEYWORDS:
        return t.value.lower()
    return None


def _collect_as_aliases(tokens: List[Token]) -> Set[str]:
    """收集所有 AS 后的别名（含列别名、表别名，覆盖 CTE 输出列被外层裸引用的情况）。"""
    aliases: Set[str] = set()
    for i, t in enumerate(tokens):
        if t.kind == "WORD" and t.value.lower() == "as":
            if i + 1 < len(tokens) and tokens[i + 1].kind == "WORD":
                aliases.add(tokens[i + 1].value.lower())
    return aliases


def _validate_columns(
    tokens: List[Token], tables: Dict[str, str], cte_names: Set[str]
) -> Optional[str]:
    """字段白名单校验；返回 None 表示通过，否则返回中文原因。"""
    alias_names = _collect_as_aliases(tokens)
    # 可出现在「表.字段」限定名左侧的引用名（别名 / 真实表名 / CTE 名）
    qual_names = set(tables.keys()) | set(tables.values()) | cte_names
    # 裸标识符可跳过校验的词：关键字 / 函数 / 表名 / 别名 / AS 别名
    skip = _KEYWORDS | _FUNCTIONS | qual_names | alias_names

    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        if t.kind != "WORD":
            i += 1
            continue

        # 限定引用：alias.col 或 alias.*
        if i + 2 < n and tokens[i + 1].kind == "DOT" and tokens[i + 2].kind in ("WORD", "STAR"):
            left = t.value.lower()
            if left not in qual_names:
                return f"未知的表/别名：{t.value}"
            if tokens[i + 2].kind == "WORD":
                right = tokens[i + 2].value.lower()
                real = tables.get(left)
                # 真实表校验字段；CTE 输出列无法静态枚举，放行
                if real in schema.TABLES and right not in TABLE_COLUMNS[real]:
                    return f"字段 {t.value}.{tokens[i + 2].value} 不在表 {real} 中"
            i += 3
            continue

        # 裸标识符：既非关键字/函数/表名/别名，也非白名单字段 → 拒绝
        low = t.value.lower()
        if low not in skip and low not in ALL_COLUMNS:
            return f"不允许的字段：{t.value}"
        i += 1

    return None


def _enforce_limit(sql: str, limit: int) -> str:
    """去除末尾分号后，缺失 LIMIT 则注入、超限则收紧。"""
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    tokens = tokenize(s)

    # 找最后一个 LIMIT 关键字
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i].kind == "WORD" and tokens[i].value.lower() == "limit":
            # LIMIT 后紧跟数字才处理
            if i + 1 < len(tokens) and tokens[i + 1].kind == "NUMBER":
                n = _parse_number(tokens[i + 1].value)
                if n is not None and n > limit:
                    num = tokens[i + 1]
                    return s[: num.start] + str(limit) + s[num.end :]
            return s  # LIMIT 后无数字（如 LIMIT ALL）保持原样
    return f"{s} LIMIT {limit}"


def _parse_number(text: str) -> Optional[int]:
    """把数字 token 文本解析为 int（失败返回 None）。"""
    try:
        return int(float(text))
    except ValueError:
        return None
