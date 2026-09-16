# -*- coding: utf-8 -*-
"""Prompt 模板与 schema 文本构建。

- build_schema_text   把 src.schema 的 TABLES 动态渲染成喂给 LLM 的文本
- build_few_shot_text 把 few_shots.FEW_SHOTS 渲染成「问题→SQL」示例块
- build_sql_messages  组装生成 SQL 的 messages（system + user）
- build_repair_messages 组装 SQL 修复（把报错反馈给 LLM）的 messages
- build_insight_messages 组装 AI 洞察的 messages（传入真实结果）
"""

from __future__ import annotations

from typing import List, Sequence

from src import schema


def build_schema_text() -> str:
    """把 schema 渲染为紧凑文本：表名 / 中文说明 / 字段 / 类型 / 示例值。"""
    lines: List[str] = []
    for table in schema.TABLES.values():
        lines.append(f"表 {table.name}（{table.comment}）：")
        for c in table.columns:
            lines.append(f"  - {c.name} {c.dtype} —— {c.description}（示例：{c.example}）")
        lines.append("")
    return "\n".join(lines)


def build_few_shot_text() -> str:
    """渲染 few-shot 示例块（问题→SQL）。"""
    from src.engine import few_shots  # 延迟导入，避免循环依赖

    lines: List[str] = []
    for i, ex in enumerate(few_shots.FEW_SHOTS, 1):
        lines.append(f"示例{i}：")
        lines.append(f"问题：{ex['question']}")
        lines.append(f"SQL：{ex['sql']}")
        lines.append("")
    return "\n".join(lines)


# 生成 SQL 的系统提示：强调只输出 SQL、只用给定表字段、只读查询、口径约定
_SQL_SYSTEM = (
    "你是「恒岳汽车零部件」经营分析系统的 Text-to-SQL 专家。"
    "你的任务：根据用户用中文提出的业务问题，结合下方数据库 schema 与示例，"
    "生成一条 **DuckDB 兼容的只读 SELECT 查询语句**。\n"
    "必须遵守：\n"
    "1. 只输出 SQL 本身，不要任何解释、注释或 markdown 代码块。\n"
    "2. 只能使用 schema 中声明的表和字段，不得臆造不存在的表/字段。\n"
    "3. 只允许 SELECT（可含 WITH 子句），禁止任何写入 / DDL 语句。\n"
    "4. 金额保留 2 位小数（用 ROUND(x, 2)），聚合结果使用有意义的列别名。\n"
    "5. 比率 / 百分比用数值计算，如 ROUND(a * 100.0 / b, 2)。\n"
    "6. 日期过滤用字符串比较或 BETWEEN，格式 'YYYY-MM-DD'。\n"
    "7. 问题含糊时输出一条最合理的查询，不要拒绝。"
)


def build_sql_messages(question: str) -> List[dict]:
    """组装生成 SQL 的 messages（system + user）。"""
    user = (
        "数据库 schema 如下：\n"
        f"{build_schema_text()}\n\n"
        "参考示例（问题→SQL）：\n"
        f"{build_few_shot_text()}\n"
        f"请为下面的问题生成 SQL：\n{question}"
    )
    return [
        {"role": "system", "content": _SQL_SYSTEM},
        {"role": "user", "content": user},
    ]


def build_repair_messages(question: str, bad_sql: str, error: str) -> List[dict]:
    """组装 SQL 修复 messages：把执行报错反馈给模型让其修正。"""
    user = (
        "我执行了你生成的 SQL 但报错了，请修正后重新输出一条 SQL（只输出 SQL 本身）。\n"
        f"原始问题：{question}\n"
        f"错误 SQL：{bad_sql}\n"
        f"报错信息：{error}\n"
        "数据库 schema 如下：\n"
        f"{build_schema_text()}"
    )
    return [
        {"role": "system", "content": _SQL_SYSTEM},
        {"role": "user", "content": user},
    ]


# 生成洞察的系统提示：强调只能基于真实数据、严禁编造
_INSIGHT_SYSTEM = (
    "你是「恒岳汽车零部件」经营分析的数据分析师。请基于给定查询结果，"
    "生成 2~3 条简洁、可读的中文业务洞察。\n"
    "严格要求：\n"
    "1. 只能基于下方提供的真实数据得出结论，严禁编造任何数据或数字；\n"
    "2. 每条洞察一句话，聚焦：数值本身、同比/环比变化、异常点或可能原因；\n"
    "3. 每条独占一行，以 '- ' 开头，不要编号、不要标题、不要空话。"
)


def build_insight_messages(
    question: str, columns: Sequence[str], rows: Sequence[Sequence]
) -> List[dict]:
    """组装 AI 洞察的 messages：把真实查询结果完整传给模型。"""
    header = " | ".join(columns)
    body = "\n".join(" | ".join(_fmt_cell(v) for v in row) for row in rows)
    user = (
        f"用户问题：{question}\n"
        f"查询结果列：{header}\n"
        f"查询结果数据（共 {len(rows)} 行）：\n{body}"
    )
    return [
        {"role": "system", "content": _INSIGHT_SYSTEM},
        {"role": "user", "content": user},
    ]


def _fmt_cell(value) -> str:
    """把单元格值安全转为字符串（None 显示为 NULL）。"""
    if value is None:
        return "NULL"
    return str(value)
