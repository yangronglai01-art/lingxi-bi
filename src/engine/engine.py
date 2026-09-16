# -*- coding: utf-8 -*-
"""问数引擎门面（Engine）：串联 Text-to-SQL → SQL 安全 → 执行 → 选图 → 洞察。

对外暴露 ask()，供 scripts/ask.py 与后续 FastAPI / Streamlit 复用。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from src.engine import chart_selector, executor, insight, sql_guard, text2sql
from src.engine.chart_selector import ChartConfig
from src.engine.executor import ExecutionError, QueryResult


class Answer(BaseModel):
    """一次完整问数的结果模型。"""

    question: str = Field("", description="用户问题")
    sql: str = Field("", description="最终执行的 SQL")
    result: Optional[QueryResult] = Field(None, description="查询结果")
    chart: Optional[ChartConfig] = Field(None, description="图表配置")
    insights: List[str] = Field(default_factory=list, description="AI 洞察")
    error: str = Field("", description="错误信息（有错误时非空）")
    mock_used: bool = Field(False, description="是否使用了 mock 降级")


class QueryEngine:
    """问数引擎：端到端把中文问题转换为 SQL、结果、图表与洞察。"""

    def ask(self, question: str) -> Answer:
        """执行完整问数流程，返回 Answer。

        流程：生成 SQL → 安全校验 → 执行（失败自动修正一次）→ 选图 → 洞察。
        任何一步失败都会在 Answer.error 中给出可读中文原因，不会抛异常。
        """
        question = (question or "").strip()
        if not question:
            return Answer(question=question, error="问题不能为空")

        mock_used = text2sql.should_use_mock()

        # 1) 生成 SQL
        try:
            sql = text2sql.generate_sql(question)
        except text2sql.Text2SQLError as exc:
            return Answer(question=question, error=str(exc), mock_used=mock_used)

        # 2) SQL 安全校验
        guarded = sql_guard.check(sql)
        if not guarded.ok:
            return Answer(
                question=question, sql=sql,
                error=f"SQL 安全校验未通过：{guarded.reason}", mock_used=mock_used,
            )

        # 3) 执行（失败则自动修正一次）
        result, final_sql, exec_err = self._execute_with_retry(question, guarded.sql, mock_used)
        if exec_err:
            return Answer(
                question=question, sql=final_sql or guarded.sql,
                error=exec_err, mock_used=mock_used,
            )

        # 4) 自动选图
        chart = chart_selector.select_chart(result, question)

        # 5) AI 洞察
        insights = insight.generate_insights(question, result, force_mock=mock_used)

        return Answer(
            question=question, sql=final_sql, result=result, chart=chart,
            insights=insights, mock_used=mock_used,
        )

    def _execute_with_retry(
        self, question: str, sql: str, mock_used: bool
    ) -> Tuple[Optional[QueryResult], str, Optional[str]]:
        """执行 SQL；失败时（非 mock 模式）让 LLM 修正一次后重试。"""
        try:
            result = executor.execute_sql(sql)
            return result, sql, None
        except ExecutionError as first_err:
            if mock_used:
                return None, sql, str(first_err)

            # 自动重试一次：把报错反馈给 LLM
            fixed = text2sql.repair_sql(question, sql, str(first_err))
            if not fixed:
                return None, sql, f"SQL 执行失败：{first_err}"

            guarded2 = sql_guard.check(fixed)
            if not guarded2.ok:
                return None, fixed, f"修正后的 SQL 仍不安全：{guarded2.reason}"

            try:
                result2 = executor.execute_sql(guarded2.sql)
                return result2, guarded2.sql, None
            except ExecutionError as second_err:
                return None, guarded2.sql, f"SQL 执行失败（已自动修正一次仍失败）：{second_err}"
