# -*- coding: utf-8 -*-
"""DuckDB 数仓构建与校验模块。

职责：
- build_warehouse        ：创建（重建）DuckDB 数仓、按 schema 建表并灌数；
- validate_and_summarize ：对灌入后的数仓做数据质量校验，并输出关键摘要
  （行数、金额逻辑自洽、日期范围、区域差异、月度趋势、良率分布），
  既是质量门禁，也是向负责人汇报的素材。
"""

from __future__ import annotations

from typing import Dict

import duckdb
import pandas as pd

from src import config, schema


def build_warehouse(dfs: Dict[str, pd.DataFrame]) -> duckdb.DuckDBPyConnection:
    """创建（或重建）DuckDB 数仓并灌入数据。

    参数:
        dfs: {表名: DataFrame} 字典（来自 data_generator.generate_all）。

    返回:
        已连接且已灌数的 DuckDB 连接（由调用方负责 close）。
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.WAREHOUSE_PATH.exists():
        config.WAREHOUSE_PATH.unlink()  # 重建，保证每次运行结果一致、可复现

    conn = duckdb.connect(str(config.WAREHOUSE_PATH))

    # 1) 按 schema 建表
    for table in schema.TABLES.values():
        conn.execute(schema.build_ddl(table))

    # 2) 灌数（显式列名，避免依赖列顺序）
    for name, df in dfs.items():
        table = schema.TABLES[name]
        cols = ", ".join(c.name for c in table.columns)
        conn.register("tmp_df", df)
        conn.execute(f"INSERT INTO {name} ({cols}) SELECT {cols} FROM tmp_df")
        conn.unregister("tmp_df")

    return conn


def validate_and_summarize(conn: duckdb.DuckDBPyConnection) -> None:
    """对数仓做质量校验并打印摘要信息。"""
    bar = "=" * 64
    print(bar)
    print("数仓校验与数据摘要")
    print(bar)

    # [1] 各表行数
    print("\n[1] 各表行数")
    for name in schema.TABLES:
        n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"    {name:<16} {n:>8,} 行")

    # [2] 逻辑自洽校验
    print("\n[2] 逻辑自洽校验（应全部为 0）")
    bad_amount = conn.execute("""
        SELECT COUNT(*) FROM fact_sales s
        JOIN dim_product p ON s.product_id = p.product_id
        WHERE ABS(s.amount - s.quantity * p.unit_price) > 0.001
    """).fetchone()[0]
    bad_cost = conn.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE cost >= amount"
    ).fetchone()[0]
    bad_fk = conn.execute("""
        SELECT COUNT(*) FROM fact_sales s
        LEFT JOIN dim_customer c ON s.customer_id = c.customer_id
        WHERE c.customer_id IS NULL
    """).fetchone()[0]
    print(f"    amount ≠ quantity×unit_price 的行数 : {bad_amount}")
    print(f"    cost ≥ amount（毛利非正）的行数    : {bad_cost}")
    print(f"    客户外键缺失的行数                  : {bad_fk}")

    # [3] 日期范围
    print("\n[3] 各事实表日期范围")
    for tbl, col in [("fact_sales", "sale_date"),
                     ("fact_quality", "check_date"),
                     ("fact_production", "prod_date")]:
        mn, mx = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {tbl}").fetchone()
        print(f"    {tbl:<16} {mn} ~ {mx}")

    # [4] 区域差异
    print("\n[4] 区域销售额（验证：华东 > 华南 > 华北 > 西南）")
    for rn, rev, pct in conn.execute("""
        SELECT r.region_name,
               ROUND(SUM(s.amount), 0),
               ROUND(SUM(s.amount) * 100.0 / SUM(SUM(s.amount)) OVER (), 1)
        FROM fact_sales s
        JOIN dim_region r ON s.region_id = r.region_id
        GROUP BY r.region_name
        ORDER BY 2 DESC
    """).fetchall():
        print(f"    {rn:<6} 销售额 {int(rev):>13,} 元  占比 {pct}%")

    # [5] 月度趋势
    print("\n[5] 月度销售额（验证：整体增长 + 季节性波动）")
    for ym, rev in conn.execute("""
        SELECT strftime(s.sale_date, '%Y-%m'), ROUND(SUM(s.amount), 0)
        FROM fact_sales s
        GROUP BY 1
        ORDER BY 1
    """).fetchall():
        print(f"    {ym}  {int(rev):>13,} 元")

    # [6] 各产品×产线 总体缺陷率
    print("\n[6] 各产品×产线 总体缺陷率")
    for line, pid, pname, rate in conn.execute("""
        SELECT q.line, q.product_id, p.product_name,
               ROUND(SUM(q.defect_count) * 100.0 / SUM(q.checked_count), 2)
        FROM fact_quality q
        JOIN dim_product p ON q.product_id = p.product_id
        GROUP BY q.line, q.product_id, p.product_name
        ORDER BY 4 DESC
    """).fetchall():
        print(f"    {line} {pid} {pname:<12} 缺陷率 {rate}%")

    # [7] P006/产线3 缺陷率按月（验证异常窗口 2026-03 ~ 05）
    print("\n[7] 轮速传感器(P006)/产线3 缺陷率按月（验证 2026-03~05 偏高）")
    for ym, dft, chk, rate in conn.execute("""
        SELECT strftime(q.check_date, '%Y-%m'),
               SUM(q.defect_count), SUM(q.checked_count),
               ROUND(SUM(q.defect_count) * 100.0 / SUM(q.checked_count), 2)
        FROM fact_quality q
        WHERE q.product_id = 'P006' AND q.line = '产线3'
        GROUP BY 1
        ORDER BY 1
    """).fetchall():
        print(f"    {ym}  缺陷 {dft:>4} / 抽检 {chk:>5}  缺陷率 {rate}%")

    print("\n" + bar)
