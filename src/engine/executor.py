# -*- coding: utf-8 -*-
"""查询执行模块：用 DuckDB 只读连接执行通过校验的 SQL。

返回 QueryResult（pydantic 模型），并把 DuckDB 原生类型转换为 JSON 可序列化的
Python 原生类型（日期→ISO 字符串、Decimal→float、bytes→str）。
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, List

import duckdb
from pydantic import BaseModel, Field

from src import config


class QueryResult(BaseModel):
    """一次查询的结果模型。"""

    columns: List[str] = Field(default_factory=list, description="列名列表")
    rows: List[List[Any]] = Field(default_factory=list, description="行数据（二维列表）")
    row_count: int = Field(0, description="返回行数")


class ExecutionError(Exception):
    """SQL 执行失败时抛出，携带可读中文错误信息。"""


def _to_native(value: Any) -> Any:
    """把 DuckDB 返回值转换为可 JSON 序列化的 Python 原生类型。"""
    if value is None:
        return None
    # bool 是 int 子类，必须先于 int 判断
    if isinstance(value, bool):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (int, float)):
        return value
    return str(value)


def execute_sql(sql: str) -> QueryResult:
    """以只读方式执行一条 SQL 并返回 QueryResult。

    参数:
        sql: 已通过 sql_guard 校验的 SELECT 语句。

    异常:
        ExecutionError: 数仓文件缺失或 SQL 执行报错。
    """
    if not config.WAREHOUSE_PATH.exists():
        raise ExecutionError(
            f"数仓文件不存在：{config.WAREHOUSE_PATH}，请先运行 scripts/build_all.py 构建"
        )

    try:
        conn = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[_to_native(v) for v in row] for row in cur.fetchall()]
            return QueryResult(columns=columns, rows=rows, row_count=len(rows))
        finally:
            conn.close()
    except ExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
        raise ExecutionError(f"SQL 执行失败：{exc}") from exc
