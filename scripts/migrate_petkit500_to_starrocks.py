# -*- coding: utf-8 -*-
"""petkit500 快照：DuckDB → StarRocks 迁移 + 控制总数对账。

历史隔离导出工具，不属于当前生产入口；只允许全新 *_migration_qa 目标。原说明保留供追溯：
  - 在 StarRocks 建库 `petkit500`，按表重建 500 张表（ENGINE=OLAP, DUPLICATE KEY, 1 副本, 1 bucket）
  - 逐表灌数（pymysql executemany 批处理）
  - 逐表行数对账 + 全局总数对账，任何不一致非零退出

用法：先 `docker compose up -d`（deploy/docker-compose.yml 拉起 StarRocks），再运行本脚本。
"""
from __future__ import annotations

import io
import os
import sys
import time

import duckdb
import pymysql

# Windows 控制台 UTF-8 输出，避免中文乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---- 源（DuckDB 快照）与目标（StarRocks）----
SRC_PATH = r"D:\projects\lingxi\data\petkit500\petkit-v1.3-638f0e235a\warehouse.duckdb"
TARGET_DB = os.getenv("STARROCKS_DATABASE", "")
HOST = os.getenv("STARROCKS_HOST", "127.0.0.1")
PORT = int(os.getenv("STARROCKS_PORT", "9030"))
USER = os.getenv("STARROCKS_USER", "")
PASSWORD = os.getenv("STARROCKS_PASSWORD", "")

BATCH = 500  # 单次 INSERT 行数

TYPE_MAP = {
    "BIGINT": "BIGINT",
    "INTEGER": "INT",
    "INT": "INT",
    "DOUBLE": "DOUBLE",
    "FLOAT": "DOUBLE",
    # 注意：StarRocks 裸 VARCHAR 会被默认成 VARCHAR(1)，导致超长值被静默过滤。
    # 故显式给最大长度 65533（StarRocks VARCHAR 上限字节数），与 DuckDB 的无界 VARCHAR 对齐。
    "VARCHAR": "VARCHAR(65533)",
    "DATE": "DATE",
    "TIMESTAMP": "DATETIME",
    "BOOLEAN": "BOOLEAN",
    "TINYINT": "TINYINT",
    "SMALLINT": "SMALLINT",
}


def map_type(duck_type: str) -> str:
    t = duck_type.strip().upper()
    if t.startswith("DECIMAL"):
        return t  # DECIMAL(p,s) 直接复用
    return TYPE_MAP.get(t, "VARCHAR(65533)")


def main() -> int:
    import re
    if (os.getenv('APP_ENV') in {'staging','production'} or os.getenv('LINGXI_LEGACY_MIGRATION_CONFIRM')!=TARGET_DB
        or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*_migration_qa',TARGET_DB)
        or HOST not in {'127.0.0.1','localhost'} or not USER):
        raise RuntimeError('Historical export only: explicit new loopback *_migration_qa database and confirmation required; use enterprise pipeline for production')
    if not os.path.exists(SRC_PATH):
        print(f"[FATAL] 源快照不存在：{SRC_PATH}")
        return 2

    src = duckdb.connect(SRC_PATH, read_only=True)
    tables = [
        r[0]
        for r in src.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
    ]
    print(f"源表数量：{len(tables)}")

    dst = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, autocommit=True, charset="utf8mb4")
    cur = dst.cursor()
    cur.execute(f"CREATE DATABASE `{TARGET_DB}`")
    cur.execute(f"USE `{TARGET_DB}`")
    print(f"独立历史迁移QA库 `{TARGET_DB}` 已创建")

    src_total = 0
    dst_total = 0
    mismatches: list[str] = []
    t0 = time.time()

    for idx, name in enumerate(tables, 1):
        cols = src.execute(f'DESCRIBE "{name}"').fetchall()  # (name, type, null, key, default, extra)
        col_defs = ", ".join(f"`{c[0]}` {map_type(str(c[1]))}" for c in cols)
        key_col = f"`{cols[0][0]}`"
        ddl = (
            f"CREATE TABLE `{name}` ({col_defs}) "
            f"ENGINE=OLAP DUPLICATE KEY({key_col}) "
            f"DISTRIBUTED BY HASH({key_col}) BUCKETS 1 "
            f"PROPERTIES ('replication_num'='1')"
        )
        cur.execute(ddl)

        rows = src.execute(f'SELECT * FROM "{name}"').fetchall()
        n_src = len(rows)
        src_total += n_src

        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f"INSERT INTO `{name}` VALUES ({placeholders})"
        for i in range(0, n_src, BATCH):
            cur.executemany(insert_sql, rows[i : i + BATCH])

        cur.execute(f"SELECT COUNT(*) FROM `{name}`")
        n_dst = cur.fetchone()[0]
        dst_total += n_dst
        if n_src != n_dst:
            mismatches.append(f"{name}: src={n_src} dst={n_dst}")

        if idx % 50 == 0 or idx == len(tables):
            print(f"  [{idx}/{len(tables)}] {name}: {n_src} 行  累计 {dst_total} 行  {time.time()-t0:.1f}s")

    print("\n===== 对账结果 =====")
    print(f"源总行数：{src_total}")
    print(f"目标总行数：{dst_total}")
    if mismatches:
        print(f"不一致表 {len(mismatches)} 张：")
        for m in mismatches[:20]:
            print("  -", m)
        return 1
    print("✅ 控制总数对账一致")
    dst.close()
    src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
