# -*- coding: utf-8 -*-
"""AI 洞察生成模块。

基于真实查询结果生成 2~3 条中文业务洞察：
- 有 API：把真实数据传给 LLM，prompt 明确约束「严禁编造数据」；
- mock / 无 API：用规则从真实结果中提炼（最大值 / 趋势 / 合计均值等），
  规则洞察同样完全基于真实返回数据，不做任何推断编造。
"""

from __future__ import annotations

from typing import Any, List, Sequence

from src import config
from src.engine import llm_client
from src.engine.executor import QueryResult
from src.engine.prompts import build_insight_messages

# 传给 LLM 的结果行数上限（避免超长上下文）
_INSIGHT_MAX_ROWS = 200


def generate_insights(question: str, result: QueryResult, force_mock: bool = False) -> List[str]:
    """生成 2~3 条中文洞察。

    参数:
        question: 用户原始问题。
        result: 真实查询结果。
        force_mock: 是否强制用规则（mock）生成。

    返回:
        洞察文本列表（最多 3 条）。
    """
    if force_mock or config.ENABLE_MOCK or not config.API_KEY:
        return _mock_insights(result)

    try:
        messages = build_insight_messages(question, result.columns, result.rows[:_INSIGHT_MAX_ROWS])
        # deepseek-v4-pro 等推理模型会先消耗 token 做思考，需给足预算才能产出最终答案
        text = llm_client.chat_completion(messages, temperature=0.3, max_tokens=3000)
        lines = [ln.strip("-• ").strip() for ln in text.splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln]
        return lines[:3] if lines else _mock_insights(result)
    except llm_client.LLMError:
        # LLM 不可用时降级为规则洞察
        return _mock_insights(result)


# ---------------------------------------------------------------------------
# 规则版洞察（完全基于真实数据）
# ---------------------------------------------------------------------------
def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v: Any) -> str:
    """数值友好的格式化：整数去小数点，浮点千分位保留 2 位。"""
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _looks_time_col(name: str) -> bool:
    return any(
        h in name.lower() for h in ("date", "日期", "月", "year", "month", "day", "日", "周")
    )


def _dim_desc(columns: Sequence[str], row: Sequence, skip_idx: int) -> str:
    """找一个非数值维度列来描述「出现位置」。"""
    for i, c in enumerate(columns):
        if i == skip_idx:
            continue
        v = row[i]
        if isinstance(v, str) and v:
            return f"{c}={v}"
    return ""


def _mock_insights(result: QueryResult) -> List[str]:
    """规则版洞察：完全基于真实返回数据，不做任何推断编造。"""
    if not result.rows:
        return ["查询无结果，可能没有符合条件的数据。"]

    rows = result.rows
    cols = result.columns
    num_idx = [i for i in range(len(cols)) if _is_num(rows[0][i])]

    insights: List[str] = []

    # 1) 单值指标：直接描述数值
    if len(rows) == 1 and num_idx:
        i = num_idx[0]
        insights.append(f"{cols[i]}为 {_fmt(rows[0][i])}。")
        return insights

    # 2) 最大值（用第一个数值列，附带其出现维度）
    if num_idx:
        i = num_idx[0]
        pairs = [(r[i], r) for r in rows if _is_num(r[i])]
        if pairs:
            mv, mr = max(pairs, key=lambda p: p[0])
            dim = _dim_desc(cols, mr, i)
            insights.append(
                f"{cols[i]} 最大值为 {_fmt(mv)}" + (f"，出现在 {dim}" if dim else "") + "。"
            )

    # 3) 时间趋势：首行与末行对比（首列像时间列时）
    if len(rows) >= 2 and num_idx and _looks_time_col(cols[0]):
        i = num_idx[0]
        first, last = rows[0][i], rows[-1][i]
        if _is_num(first) and _is_num(last) and first:
            chg = (last - first) / abs(first) * 100
            direction = "增长" if chg >= 0 else "下降"
            insights.append(
                f"{cols[i]} 从 {_fmt(first)} 变为 {_fmt(last)}（{direction} {abs(chg):.2f}%）。"
            )

    # 4) 合计与均值
    if num_idx and len(rows) > 1:
        i = num_idx[0]
        vals = [r[i] for r in rows if _is_num(r[i])]
        if vals:
            total = sum(vals)
            avg = total / len(vals)
            insights.append(f"{cols[i]} 合计 {_fmt(total)}，平均 {_fmt(avg)}。")

    return insights[:3] or [f"共返回 {len(rows)} 行数据。"]
