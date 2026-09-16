# -*- coding: utf-8 -*-
"""Text-to-SQL 引擎：把中文问题转换为 DuckDB SELECT 语句。

- 优先调用 LLM（OpenAI 兼容接口）生成 SQL；
- 无 API / ENABLE_MOCK 时降级为「关键词 → 模板 SQL」规则生成；
- 提供 repair_sql，可把执行报错反馈给 LLM 让其自动修正一次。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from src import config
from src.engine import llm_client
from src.engine.prompts import build_repair_messages, build_sql_messages


class Text2SQLError(Exception):
    """SQL 生成失败时抛出。"""


def should_use_mock() -> bool:
    """是否应使用 mock 降级：显式开启 ENABLE_MOCK，或未配置 API Key。"""
    return config.ENABLE_MOCK or not config.API_KEY


def generate_sql(question: str) -> str:
    """根据中文问题生成 SQL（LLM 优先，mock 兜底）。

    异常:
        Text2SQLError: LLM 不可用且 mock 规则也无法匹配。
    """
    if should_use_mock():
        sql = mock_generate_sql(question)
        if not sql:
            raise Text2SQLError("无法用规则匹配生成 SQL，且未配置可用的大模型")
        return sql

    try:
        messages = build_sql_messages(question)
        sql = llm_client.chat_completion(messages, temperature=0.0, max_tokens=2000)
        sql = _extract_sql(sql)
        if not sql:
            raise Text2SQLError("大模型未返回有效 SQL")
        return sql
    except llm_client.LLMError as exc:
        # 网络 / 鉴权失败 → 降级 mock
        sql = mock_generate_sql(question)
        if sql:
            return sql
        raise Text2SQLError(f"大模型调用失败且 mock 无法匹配：{exc}") from exc


def repair_sql(question: str, bad_sql: str, error: str) -> Optional[str]:
    """把执行报错反馈给 LLM，让它修正 SQL（用于自动重试一次）。"""
    if should_use_mock():
        return None  # mock 模式无自省能力，无法修正
    try:
        messages = build_repair_messages(question, bad_sql, error)
        sql = llm_client.chat_completion(messages, temperature=0.0, max_tokens=2000)
        return _extract_sql(sql) or None
    except llm_client.LLMError:
        return None


def _extract_sql(text: str) -> str:
    """从 LLM 输出中提取 SQL（容忍 markdown 代码块包裹与末尾分号）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        lines = t.splitlines()
        if lines and lines[0].strip().lower().startswith("sql"):
            lines = lines[1:]
        t = "\n".join(lines).strip()
    return t.strip().rstrip(";").strip()


# ---------------------------------------------------------------------------
# mock 降级：关键词 → 模板 SQL（离线也能跑通全流程）
# ---------------------------------------------------------------------------
def _mock_total() -> str:
    return "SELECT ROUND(SUM(amount), 2) AS total_amount FROM fact_sales"


def _mock_region() -> str:
    return (
        "SELECT r.region_name, ROUND(SUM(s.amount), 2) AS total_amount "
        "FROM fact_sales s JOIN dim_region r ON s.region_id = r.region_id "
        "GROUP BY r.region_name ORDER BY total_amount DESC"
    )


def _mock_monthly() -> str:
    return (
        "SELECT strftime(s.sale_date, '%Y-%m') AS ym, ROUND(SUM(s.amount), 2) AS total_amount "
        "FROM fact_sales s GROUP BY ym ORDER BY ym"
    )


def _mock_product_top() -> str:
    return (
        "SELECT p.product_name, ROUND(SUM(s.amount), 2) AS total_amount "
        "FROM fact_sales s JOIN dim_product p ON s.product_id = p.product_id "
        "GROUP BY p.product_name ORDER BY total_amount DESC LIMIT 5"
    )


def _mock_quality() -> str:
    return (
        "SELECT p.product_name, ROUND(SUM(q.defect_count) * 100.0 / SUM(q.checked_count), 2) AS defect_rate "
        "FROM fact_quality q JOIN dim_product p ON q.product_id = p.product_id "
        "GROUP BY p.product_name ORDER BY defect_rate DESC"
    )


def _mock_production() -> str:
    return (
        "SELECT strftime(p.prod_date, '%Y-%m') AS ym, "
        "ROUND(SUM(p.actual_qty) * 100.0 / SUM(p.planned_qty), 2) AS achievement_rate "
        "FROM fact_production p GROUP BY ym ORDER BY ym"
    )


def _mock_margin() -> str:
    return (
        "SELECT ROUND((SUM(s.amount) - SUM(s.cost)) * 100.0 / SUM(s.amount), 2) AS gross_margin_rate "
        "FROM fact_sales s"
    )


def _mock_customer() -> str:
    return (
        "SELECT c.customer_name, ROUND(SUM(s.amount), 2) AS total_amount "
        "FROM fact_sales s JOIN dim_customer c ON s.customer_id = c.customer_id "
        "GROUP BY c.customer_name ORDER BY total_amount DESC"
    )


def _mock_avg_daily() -> str:
    return "SELECT ROUND(SUM(amount) / COUNT(DISTINCT sale_date), 2) AS avg_daily_amount FROM fact_sales"


# 关键词 → 生成函数 的规则表（按优先级匹配，命中即返回）
_MOCK_RULES: List[Tuple[Tuple[str, ...], object]] = [
    (("缺陷率", "不合格", "良率", "质量"), _mock_quality),
    (("达成率", "产量", "生产", "停机"), _mock_production),
    (("毛利", "毛利率", "利润"), _mock_margin),
    (("区域", "地区"), _mock_region),
    (("客户",), _mock_customer),
    (("产品", "top", "最高", "排名", "畅销"), _mock_product_top),
    (("趋势", "按月", "每月", "逐月", "月度"), _mock_monthly),
    (("平均",), _mock_avg_daily),
    (("销售额", "收入", "金额", "销售"), _mock_total),
]


def mock_generate_sql(question: str) -> Optional[str]:
    """按关键词规则生成模板 SQL；无匹配返回 None。"""
    q = question.lower()
    for keywords, fn in _MOCK_RULES:
        if any(k.lower() in q for k in keywords):
            return fn()  # type: ignore[operator]
    return None
