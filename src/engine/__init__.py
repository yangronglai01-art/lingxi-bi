# -*- coding: utf-8 -*-
"""核心引擎层（Text-to-SQL / SQL 安全 / 执行 / 自动选图 / AI 洞察）。

模块划分：
- llm_client      大模型客户端（OpenAI 兼容 Chat Completions）
- prompts         Prompt 模板与 schema 文本构建
- few_shots       Text-to-SQL few-shot 示例集
- text2sql        Text-to-SQL 引擎（LLM 生成 + mock 降级 + 修复重试）
- sql_guard       SQL 安全校验（仅 SELECT / 单语句 / 白名单 / LIMIT 上限）
- executor        DuckDB 只读执行 + QueryResult 模型
- chart_selector  自动选图（规则）
- insight         AI 洞察生成（LLM + mock 规则）
- engine          问数引擎门面（串联全流程，对外暴露 ask）
"""

from src.engine.engine import Answer, QueryEngine

__all__ = ["QueryEngine", "Answer"]
