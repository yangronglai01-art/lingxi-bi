# -*- coding: utf-8 -*-
"""自动选图模块：基于查询结果结构，用规则（不用 LLM）决定图表类型。

选图规则（按优先级）：
1. 单指标单值（1 列 1 行）→ indicator（指标卡）
2. 含日期/时间列且含数值列 → line（折线图）
3. 1 个维度 + 1 个数值：
   - 维度取值少（<= PIE_MAX_CATEGORIES）、数值全非负、且语义含「占比」→ pie（饼图）
   - 否则 → bar（柱状图，分组对比）
4. 其余（明细多列 / 多维度 / 无清晰数值）→ table（表格）
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.engine.executor import QueryResult

# 饼图最多展示的类别数（超过则退化为柱状图，避免饼图过碎）
PIE_MAX_CATEGORIES = 8

# 日期列名关键字（用于辅助识别时间列）
_DATE_NAME_HINTS = ("date", "日期", "年月", "月份", "month", "year", "周", "week", "day", "日")

# 判定「占比」语义的关键字（问题或数值列名中出现即视为占比有意义）
_PROPORTION_HINTS = ("占比", "比例", "份额", "百分比", "构成", "分布", "%")

# 日期值格式：YYYY-MM 或 YYYY-MM-DD
_DATE_VALUE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


class ChartConfig(BaseModel):
    """图表渲染配置（供前端使用）。"""

    chart_type: str = Field(..., description="图表类型：indicator/line/bar/pie/table")
    title: str = Field("", description="图表标题")
    x: Optional[str] = Field(None, description="维度列名（line/bar/pie）")
    y: List[str] = Field(default_factory=list, description="数值列名")
    labels: List[str] = Field(default_factory=list, description="维度取值")
    values: List[float] = Field(default_factory=list, description="数值取值")
    reason: str = Field("", description="选图依据说明")


def _is_number(v: Any) -> bool:
    """判断值是否为数值（排除 bool）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _looks_like_date(v: Any) -> bool:
    """判断值是否形如日期字符串（YYYY-MM / YYYY-MM-DD）。"""
    return isinstance(v, str) and bool(_DATE_VALUE_RE.match(v))


def _column_kind(result: QueryResult, col_idx: int) -> str:
    """判断某列类型：date / measure / dimension。"""
    name = result.columns[col_idx]
    # 列名含日期关键字 → 直接判定为时间列
    if any(h in name.lower() for h in _DATE_NAME_HINTS):
        return "date"

    values = [row[col_idx] for row in result.rows if col_idx < len(row)]
    if not values:
        return "dimension"
    # 全为数值（含 None）→ 数值列
    if all(_is_number(v) or v is None for v in values) and any(_is_number(v) for v in values):
        return "measure"
    # 全为日期字符串 → 时间列
    if all(_looks_like_date(v) or v is None for v in values) and any(_looks_like_date(v) for v in values):
        return "date"
    return "dimension"


def _hints_proportion(text: str) -> bool:
    """判断文本是否暗示「占比」语义。"""
    return any(k in text for k in _PROPORTION_HINTS)


def select_chart(result: QueryResult, question: str = "") -> ChartConfig:
    """根据查询结果结构自动选择图表类型，返回 ChartConfig。"""
    n_cols = len(result.columns)
    n_rows = result.row_count

    # 1) 单指标单值 → 指标卡
    if n_cols == 1 and n_rows == 1:
        col = result.columns[0] if result.columns else ""
        v = result.rows[0][0] if result.rows and len(result.rows[0]) else 0
        return ChartConfig(
            chart_type="indicator",
            title=question or col,
            y=[col] if col else [],
            values=[float(v)] if _is_number(v) else [],
            reason="单指标单值，适合用指标卡突出显示",
        )

    kinds = [_column_kind(result, i) for i in range(n_cols)]
    measure_idx = [i for i, k in enumerate(kinds) if k == "measure"]
    date_idx = [i for i, k in enumerate(kinds) if k == "date"]
    dim_idx = [i for i, k in enumerate(kinds) if k == "dimension"]

    # 2) 含日期列 + 至少一个数值列 → 折线图
    if date_idx and measure_idx:
        x = result.columns[date_idx[0]]
        y = [result.columns[i] for i in measure_idx]
        labels = [str(row[date_idx[0]]) for row in result.rows if date_idx[0] < len(row)]
        values = (
            [float(row[measure_idx[0]]) for row in result.rows
             if measure_idx[0] < len(row) and _is_number(row[measure_idx[0]])]
        )
        return ChartConfig(
            chart_type="line", title=question or "时间趋势", x=x, y=y,
            labels=labels, values=values, reason="包含日期/时间列，适合折线图展示趋势",
        )

    # 3) 1 个维度 + 1 个数值 → 柱状图 / 饼图
    if len(dim_idx) == 1 and len(measure_idx) == 1:
        dim, meas = dim_idx[0], measure_idx[0]
        x = result.columns[dim]
        y = [result.columns[meas]]
        labels = [str(row[dim]) for row in result.rows if dim < len(row)]
        values = [
            float(row[meas]) for row in result.rows
            if meas < len(row) and _is_number(row[meas])
        ]
        distinct = len(set(labels))
        # 占比有意义：类别少 且 数值全非负 且 语义含占比
        if (
            distinct <= PIE_MAX_CATEGORIES
            and values
            and all(v >= 0 for v in values)
            and _hints_proportion(question + y[0])
        ):
            return ChartConfig(
                chart_type="pie", title=question or "占比", x=x, y=y,
                labels=labels, values=values, reason="单维度单数值、类别少且语义为占比，适合饼图",
            )
        return ChartConfig(
            chart_type="bar", title=question or "分组对比", x=x, y=y,
            labels=labels, values=values, reason="单维度单数值，适合柱状图展示分组对比",
        )

    # 4) 其余 → 表格
    return ChartConfig(
        chart_type="table", title=question or "明细数据",
        reason="多列明细或结构复杂，适合表格展示",
    )
