"""中央 PostgreSQL 控制面存储（取代 SQLite）。

所有控制面持久化（身份、令牌、任务、审计、运行时状态、历史、接入台账）都落在一个
PG17 实例里。本模块提供一个 psycopg2 连接工厂，以及一个兼容 sqlite3 风格的光标门面，
让既有调用点以最小改动迁移过来。

参考成熟 NL2SQL 项目（WrenAI / Vanna / Dataherald）的做法：单一共享 Postgres 存储 +
一个薄薄的查询辅助层，而不是各文件各自 `sqlite3.connect`。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import DictCursor

from src import config

# 供调用方捕获「存储不可用」类异常（对应旧的 sqlite3.Error）。
Error = psycopg2.Error


def current_database() -> str:
    """当前控制面数据库名。

    ``config.PG_DATABASE`` 在导入时缓存，因此若同一进程内需要切换到「隔离实例」的独立
    数据库（验收 / 评估脚本），可通过 ``os.environ["PG_DATABASE"]`` 动态覆盖。子进程
    （worker / api）天然继承该环境变量。
    """
    return os.environ.get("PG_DATABASE") or config.PG_DATABASE


def connect():
    """打开一条到控制面 Postgres 的新连接（DictCursor）。"""
    return psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        dbname=current_database(),
        cursor_factory=DictCursor,
    )


def _translate(sql: str) -> str:
    """把 SQLite 风格的 ``?`` 占位符转成 psycopg2 的 ``%s``。

    控制面查询里的 ``?`` 全部是占位符（字符串字面量里不含 ``?``），因此简单替换是安全
    的。这样迁移时无需逐条改写已有查询。
    """
    return sql.replace("?", "%s")


class Cursor:
    """对 psycopg2 DictCursor 的 sqlite3 风格门面。

    复刻代码库中用到的 sqlite3 游标 API 子集：``execute()`` 返回 ``self``（因此
    ``c.execute(...).fetchone()`` 能继续工作）；行同时支持整数下标与列名下标
    （DictRow 是 list 的子类）；``dict(row)`` 得到 JSON 就绪的映射。
    """

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()

    def execute(self, sql, params=()):
        self._cur.execute(_translate(sql), params)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(_translate(sql), seq)
        return self

    def executescript(self, sql):
        # DDL 无参数；psycopg2 通过 simple-query 协议一次执行多条以分号分隔的语句。
        self._cur.execute(sql)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        # 兼容旧 sqlite3 游标的可迭代语义（``for r in c.execute(...)``）。
        return iter(self._cur.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


@contextmanager
def db():
    """事务型游标上下文：成功则提交，出错则回滚。

    与旧 sqlite3 的 ``db()`` 语义一致：进入即开启隐式事务，``yield`` 一个游标门面，
    正常退出提交、异常退出回滚。
    """
    conn = connect()
    try:
        yield Cursor(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
