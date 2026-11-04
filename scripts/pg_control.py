"""PG17 控制面迁移辅助（scripts 共用）。

旧脚本以 ``sqlite3.connect(data/'control.sqlite3')`` 直连控制面；迁移后控制面是共享的
PostgreSQL 17。本模块把下面这些跨脚本的语义收敛到一处：

* ``db_name_for(data_dir)`` —— 把「数据目录」稳定映射为「控制面数据库名」：
  主实例（``data/petkit500``）→ ``config.PG_DATABASE``（默认 ``lingxi``）；
  隔离/恢复演练目录 → ``lingxi_eval_<sha1(path)[:12]>``（确定性、可跨脚本复用）。
* ``clone_control_plane(data_dir, source_dir)`` —— 为隔离实例建库、建表并复制
  users/tokens（旧脚本里的 ``origin.backup(target)``）。
* ``control_rows`` / ``control_scalar`` / ``jobs_fingerprint`` / ``identity_fingerprint``
  —— 只读探测（旧脚本里的 ``sqlite3.connect(...).execute(...)``）。
* ``control_db_size(data_dir)`` —— 库体积（旧脚本里统计 ``control.sqlite3`` 文件大小）。
* ``pg_dump_control`` / ``restore_dump`` —— 一致逻辑备份与恢复演练（容器内 pg_dump/psql）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extras import DictCursor

from src import config
from src.storage import Cursor, _translate

# docker 里 postgres 容器名（pg_dump / psql 在容器内），可用环境变量覆盖。
PG_CONTAINER = os.getenv("PG_CONTAINER", "lingxi-postgres")

# 主实例数据目录（即 provision_simulated.py 播种的 canonical 目录）。
MAIN_DATA_DIR = (ROOT / "data" / "petkit500").resolve()


def db_name_for(data_dir) -> str:
    """数据目录 → 控制面数据库名。"""
    d = Path(data_dir).resolve()
    if d == MAIN_DATA_DIR:
        return config.PG_DATABASE
    h = hashlib.sha1(str(d).encode("utf-8")).hexdigest()[:12]
    return f"lingxi_eval_{h}"


def _conn(dbname: str):
    return psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        dbname=dbname,
        cursor_factory=DictCursor,
    )


def admin_conn():
    """连到 ``postgres`` 库（autocommit），用于 CREATE/DROP DATABASE 与库大小查询。"""
    conn = psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        dbname="postgres",
    )
    conn.autocommit = True
    return conn


def database_exists(name: str) -> bool:
    conn = admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _drop_and_create(name: str) -> None:
    conn = admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
            if cur.fetchone():
                cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        conn.close()


def initialize_schema(dbname: str) -> None:
    """在指定库中执行控制面 DDL（复用 ``control.initialize()`` 的唯一定义）。"""
    from src.petkit import control
    prev = os.environ.get("PG_DATABASE")
    os.environ["PG_DATABASE"] = dbname
    try:
        control.initialize()
    finally:
        if prev is None:
            os.environ.pop("PG_DATABASE", None)
        else:
            os.environ["PG_DATABASE"] = prev


@contextmanager
def control_cursor(dbname: str):
    """对指定库的 sqlite3 风格游标上下文（成功提交、异常回滚）。"""
    conn = _conn(dbname)
    try:
        yield Cursor(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def control_rows(data_dir, sql: str, params=()):
    """对 data_dir 对应控制面库执行查询，返回行列表（下标/列名均可访问）。"""
    with control_cursor(db_name_for(data_dir)) as c:
        return c.execute(sql, params).fetchall()


def control_scalar(data_dir, sql: str, params=()):
    rows = control_rows(data_dir, sql, params)
    return rows[0][0] if rows else None


def jobs_fingerprint(data_dir) -> str:
    rows = control_rows(data_dir, "SELECT id,state,updated FROM jobs ORDER BY id")
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


def identity_fingerprint(data_dir) -> str:
    rows = control_rows(
        data_dir,
        "SELECT subject,department,role,regions,enabled,version FROM users ORDER BY subject",
    )
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


def control_db_size(data_dir) -> int:
    """控制面库在磁盘上的体积（字节），替代旧的控制面文件大小统计。"""
    name = db_name_for(data_dir)
    conn = admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(%s)", (name,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def clone_control_plane(data_dir, source_dir=None) -> str:
    """为 data_dir 建立独立控制面库（建库+建表+复制 users/tokens）。返回库名。

    ``source_dir=None`` 表示从主实例库（``config.PG_DATABASE``）复制账号与令牌。
    """
    name = db_name_for(data_dir)
    source_db = db_name_for(source_dir) if source_dir is not None else config.PG_DATABASE
    if name == config.PG_DATABASE:
        return name  # 主实例无需克隆
    _drop_and_create(name)
    initialize_schema(name)
    if source_db and source_db != name:
        src = _conn(source_db)
        dst = _conn(name)
        try:
            with src.cursor() as s, dst.cursor() as d:
                s.execute(
                    "SELECT subject,department,role,regions,enabled,version FROM users ORDER BY subject"
                )
                users = s.fetchall()
                if users:
                    d.executemany(
                        "INSERT INTO users(subject,department,role,regions,enabled,version) "
                        "VALUES(%s,%s,%s,%s,%s,%s)",
                        users,
                    )
                s.execute("SELECT digest,subject,created FROM tokens")
                tokens = s.fetchall()
                if tokens:
                    d.executemany(
                        "INSERT INTO tokens(digest,subject,created) VALUES(%s,%s,%s)", tokens
                    )
        finally:
            src.close()
            dst.commit()
            dst.close()
    return name


def drop_isolated_database(data_dir) -> None:
    """删除 data_dir 对应的隔离库（若存在）。"""
    name = db_name_for(data_dir)
    if name == config.PG_DATABASE:
        return
    conn = admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
            if cur.fetchone():
                cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        conn.close()


def pg_dump_control(dest_path, dbname: str | None = None):
    """对控制面库做一致逻辑备份（容器内 ``pg_dump``）。返回目标路径。"""
    dbname = dbname or config.PG_DATABASE
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "exec", "-e", f"PGPASSWORD={config.PG_PASSWORD}", PG_CONTAINER,
        "pg_dump", "-U", config.PG_USER, "-d", dbname,
        "--no-owner", "--no-privileges", "--no-comments",
    ]
    with open(dest, "wb") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, check=True)
    return dest


def restore_dump(dump_path, dbname: str) -> str:
    """把 ``pg_dump`` 产物恢复到指定库（先重建库）。返回库名。"""
    _drop_and_create(dbname)
    cmd = [
        "docker", "exec", "-i", "-e", f"PGPASSWORD={config.PG_PASSWORD}", PG_CONTAINER,
        "psql", "-U", config.PG_USER, "-d", dbname, "-v", "ON_ERROR_STOP=1", "-q",
    ]
    with open(dump_path, "rb") as f:
        subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, check=True)
    return dbname


def table_count(dbname: str, table: str) -> int:
    with _conn(dbname) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        return cur.fetchone()[0]
