# -*- coding: utf-8 -*-
"""FastAPI 接口层：把「问数引擎」包装为 HTTP 服务。

对外提供健康检查、数据字典、中文问数、历史记录与收藏等接口，
供 Streamlit 工作台或任何第三方前端调用。

接口一览：
- GET  /health                健康检查
- GET  /schema                数据字典（6 张表 + 字段中文说明 + 外键关系）
- POST /ask                   中文问数（body: {"question": "..."}）
- GET  /history               历史记录列表（?favorite_only=true 只看收藏）
- GET  /history/{id}          单条历史详情
- POST /history/{id}/favorite 收藏 / 取消收藏
- DELETE /history/{id}        删除一条历史

错误处理约定：/ask 永不抛 500；引擎内部错误统一落到 Answer.error 字段，
HTTP 始终返回 200，便于前端直接渲染「结果 or 错误提示」。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src import config, schema
from src.engine.chart_selector import ChartConfig
from src.engine.engine import Answer, QueryEngine
from src.engine.executor import QueryResult
from src.history import HistoryStore

# ---------------------------------------------------------------------------
# 应用与全局对象
# ---------------------------------------------------------------------------
app = FastAPI(
    title="宁波鲍斯能源装备 · 智能问数平台 API",
    description="中文自然语言问数接口：问题 → SQL → 结果 → 图表 → 洞察",
    version="1.0.0",
)

# 允许跨域（本地开发前端通常运行在 8501 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 引擎单例（每次 ask 都会连接 DuckDB 只读查询，无状态，可安全复用）
engine = QueryEngine()

# 历史记录仓库（与 Streamlit 共用同一个 data/history.db）
store = HistoryStore()


# ---------------------------------------------------------------------------
# 请求 / 响应模型（pydantic）
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    """问数请求体。"""

    question: str = Field(..., min_length=1, max_length=500, description="用户中文问题")


class AskResponse(BaseModel):
    """问数响应体：完整复用引擎 Answer 结构，并附带历史记录 ID。"""

    question: str
    sql: str
    result: Optional[QueryResult]
    chart: Optional[ChartConfig]
    insights: List[str]
    error: str
    mock_used: bool
    history_id: Optional[int] = None


class FavoriteResponse(BaseModel):
    """收藏操作响应。"""

    id: int
    favorited: bool


# ---------------------------------------------------------------------------
# 健康检查 / 数据字典
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """健康检查：返回服务状态与数仓就绪情况。"""
    return {
        "status": "ok",
        "warehouse_ready": config.WAREHOUSE_PATH.exists(),
        "model": config.MODEL_NAME,
    }


@app.get("/schema")
def get_schema() -> dict:
    """数据字典：返回 6 张表的字段与中文说明，供前端展示或下游调用。"""
    tables = []
    for t in schema.TABLES.values():
        tables.append(
            {
                "name": t.name,
                "comment": t.comment,
                "columns": [asdict(c) for c in t.columns],
            }
        )
    return {
        "tables": tables,
        "foreign_keys": schema.FOREIGN_KEYS,
        "data_range": {"start": config.START_DATE, "end": config.END_DATE},
    }


# ---------------------------------------------------------------------------
# 问数
# ---------------------------------------------------------------------------
@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """中文问数：调用引擎完成 Text-to-SQL → 执行 → 选图 → 洞察全流程。

    无论成功失败都保存一条历史，便于回溯问题。
    """
    answer: Answer = engine.ask(req.question)

    history_id = store.add(
        question=answer.question,
        sql=answer.sql,
        result=answer.result,
        chart=answer.chart,
        insights=answer.insights,
        error=answer.error,
        mock_used=answer.mock_used,
    )

    return AskResponse(
        question=answer.question,
        sql=answer.sql,
        result=answer.result,
        chart=answer.chart,
        insights=answer.insights,
        error=answer.error,
        mock_used=answer.mock_used,
        history_id=history_id,
    )


# ---------------------------------------------------------------------------
# 历史 / 收藏
# ---------------------------------------------------------------------------
@app.get("/history")
def list_history(favorite_only: bool = False, limit: int = 100) -> dict:
    """历史记录列表（倒序）。"""
    return {"items": store.list(favorite_only=favorite_only, limit=limit)}


@app.get("/history/{history_id}")
def get_history(history_id: int) -> dict:
    """单条历史详情。"""
    item = store.get(history_id)
    if item is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return item


@app.post("/history/{history_id}/favorite", response_model=FavoriteResponse)
def toggle_favorite(history_id: int) -> FavoriteResponse:
    """收藏 / 取消收藏。"""
    new_val = store.toggle_favorite(history_id)
    if new_val is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return FavoriteResponse(id=history_id, favorited=bool(new_val))


@app.delete("/history/{history_id}")
def delete_history(history_id: int) -> dict:
    """删除一条历史。"""
    if not store.delete(history_id):
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return {"ok": True, "id": history_id}
