# -*- coding: utf-8 -*-
"""数据库执行适配层（参考 Dataherald 的 connector 抽象思路）。

同一套连接接口，双后端：
  - DuckDB 后端：开发 / 单元测试（默认，向后兼容 397 项现有测试）
  - StarRocks 后端：生产（MySQL 协议，pymysql）

连接对象约定接口（与 duckdb.connect(...) 对齐，便于 executor/execution 无痛切换）：
  - execute(sql) -> cursor（含 .description / .fetchall() / .fetchmany(n)）
  - interrupt()  ：取消进行中的查询（best-effort）
  - close()      ：释放连接
"""
from __future__ import annotations

from typing import Any

from src import config


class DuckDBConnection:
    """DuckDB 后端：本地文件只读连接（开发 / 测试）。"""

    def __init__(self, database: str, read_only: bool = True, memory_limit: str | None = None):
        import duckdb

        opts = {}
        if memory_limit:
            opts["memory_limit"] = memory_limit
        self._conn = duckdb.connect(database, read_only=read_only, config=opts)

    def execute(self, sql: str):
        return self._conn.execute(sql)

    def interrupt(self) -> None:
        self._conn.interrupt()

    def close(self) -> None:
        self._conn.close()


class StarRocksConnection:
    """StarRocks 后端：MySQL 协议连接（生产）。

    interrupt() 通过一条独立管理连接 KILL 本连接的 server thread，
    实现与 duckdb 的 conn.interrupt() 语义对齐的「取消进行中查询」（best-effort）。
    """

    def __init__(self, host: str, port: int, user: str, password: str, database: str | None = None):
        import pymysql

        self._params = dict(host=host, port=port, user=user, password=password, database=database)
        self._conn = pymysql.connect(
            **self._params,
            autocommit=True,
            connect_timeout=10,
            read_timeout=config.REQUEST_TIMEOUT,
            charset="utf8mb4",
        )
        self._thread_id = self._conn.thread_id()
        self._closed = False

    def execute(self, sql: str):
        cur = self._conn.cursor()
        cur.execute(sql)
        return cur

    def interrupt(self) -> None:
        if self._closed:
            return
        try:
            import pymysql

            killer = pymysql.connect(**self._params, autocommit=True, connect_timeout=5)
            try:
                killer.cursor().execute(f"KILL {self._thread_id}")
            finally:
                killer.close()
        except Exception:
            # 取消是 best-effort：查询超时（read_timeout）仍是兜底
            pass

    def close(self) -> None:
        if not self._closed:
            try:
                self._conn.close()
            finally:
                self._closed = True


def date_fmt(col: str, fmt: str = "%Y-%m") -> str:
    """按当前查询引擎方言返回日期格式化表达式。

    - duckdb    -> strftime(col, fmt)
    - starrocks -> date_format(col, fmt)

    两边的格式串语法一致（均为 %Y/%m/%d 等），因此同一 fmt 可复用。
    供 planning / text2sql / few_shots / eval_cases 等 SQL 模板统一调用，
    避免模板里硬编码 strftime 导致切到 StarRocks 后报函数不存在。
    """
    if config.QUERY_ENGINE == "starrocks":
        return f"date_format({col}, '{fmt}')"
    return f"strftime({col}, '{fmt}')"


def connect(database: str | None = None, read_only: bool = True, memory_limit: str | None = None):
    """按 config.QUERY_ENGINE 返回对应后端连接。

    DuckDB 的 database 参数语义是文件路径；StarRocks 的 database 参数语义是库名。
    """
    if config.QUERY_ENGINE == "starrocks":
        return StarRocksConnection(
            host=config.STARROCKS_HOST,
            port=config.STARROCKS_PORT,
            user=config.STARROCKS_USER,
            password=config.STARROCKS_PASSWORD,
            database=database or config.STARROCKS_DATABASE,
        )
    return DuckDBConnection(
        database or str(config.WAREHOUSE_PATH),
        read_only=read_only,
        memory_limit=memory_limit,
    )
