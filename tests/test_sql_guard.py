# -*- coding: utf-8 -*-
"""sql_guard 安全校验单元测试。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import sql_guard  # noqa: E402


# ---------------------------------------------------------------------------
# 放行用例
# ---------------------------------------------------------------------------
def test_allow_simple_select():
    r = sql_guard.check("SELECT ROUND(SUM(amount), 2) AS total_amount FROM fact_sales")
    assert r.ok, r.reason
    assert "LIMIT" in r.sql.upper()


def test_allow_join_and_group():
    r = sql_guard.check(
        "SELECT r.region_name, ROUND(SUM(s.amount), 2) AS total_amount "
        "FROM fact_sales s JOIN dim_region r ON s.region_id = r.region_id "
        "GROUP BY r.region_name ORDER BY total_amount DESC"
    )
    assert r.ok, r.reason


def test_allow_cte():
    r = sql_guard.check(
        "WITH monthly AS (SELECT strftime(sale_date, '%Y-%m') AS ym, SUM(amount) AS total "
        "FROM fact_sales GROUP BY ym) SELECT ym, total FROM monthly ORDER BY ym"
    )
    assert r.ok, r.reason


def test_allow_star_and_qualified():
    r = sql_guard.check("SELECT s.* FROM fact_sales s")
    assert r.ok, r.reason


# ---------------------------------------------------------------------------
# 拒绝用例：非 SELECT / 写入 / DDL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dim_region VALUES ('R999', 'X', 'X')",
        "UPDATE dim_region SET region_name='X'",
        "DELETE FROM fact_sales",
        "DROP TABLE fact_sales",
        "ALTER TABLE dim_region ADD COLUMN x INT",
        "CREATE TABLE t (x INT)",
        "PRAGMA database_list",
        "ATTACH 'x.db' AS x",
        "TRUNCATE fact_sales",
    ],
)
def test_reject_forbidden_statements(sql):
    r = sql_guard.check(sql)
    assert not r.ok, sql


# ---------------------------------------------------------------------------
# 拒绝用例：多语句
# ---------------------------------------------------------------------------
def test_reject_multiple_statements():
    r = sql_guard.check("SELECT 1; SELECT 2")
    assert not r.ok


# ---------------------------------------------------------------------------
# 拒绝用例：表 / 字段白名单
# ---------------------------------------------------------------------------
def test_reject_unknown_table():
    r = sql_guard.check("SELECT * FROM secret_table")
    assert not r.ok
    assert "表" in r.reason


def test_reject_unknown_column():
    r = sql_guard.check("SELECT nonexistent_col FROM fact_sales")
    assert not r.ok
    assert "字段" in r.reason


def test_reject_unknown_qualified_column():
    r = sql_guard.check("SELECT s.ammount FROM fact_sales s")
    assert not r.ok


def test_reject_unknown_alias():
    r = sql_guard.check("SELECT x.amount FROM fact_sales s")
    assert not r.ok


# ---------------------------------------------------------------------------
# LIMIT 上限：注入 / 收紧
# ---------------------------------------------------------------------------
def test_enforce_limit_append():
    r = sql_guard.check("SELECT * FROM dim_region")
    assert r.ok
    assert r.sql.upper().endswith("LIMIT 1000")


def test_enforce_limit_clamp():
    r = sql_guard.check("SELECT * FROM dim_region LIMIT 5000")
    assert r.ok
    assert "LIMIT 1000" in r.sql.upper()


def test_limit_within_bound_kept():
    r = sql_guard.check("SELECT * FROM dim_region LIMIT 5")
    assert r.ok
    assert "LIMIT 5" in r.sql.upper()


def test_trailing_semicolon_ok():
    r = sql_guard.check("SELECT * FROM dim_region;")
    assert r.ok
    assert r.sql.upper().endswith("LIMIT 1000")


# ---------------------------------------------------------------------------
# 空 / 非法输入
# ---------------------------------------------------------------------------
def test_reject_empty():
    r = sql_guard.check("")
    assert not r.ok
