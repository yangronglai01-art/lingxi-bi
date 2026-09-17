# -*- coding: utf-8 -*-
"""chart_selector 自动选图单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.chart_selector import select_chart  # noqa: E402
from src.engine.executor import QueryResult  # noqa: E402


def _mk(columns, rows):
    return QueryResult(columns=columns, rows=rows, row_count=len(rows))


# ---------------------------------------------------------------------------
# 指标卡
# ---------------------------------------------------------------------------
def test_indicator_single_value():
    r = _mk(["total_amount"], [[1234567.0]])
    c = select_chart(r, "全年总销售额")
    assert c.chart_type == "indicator"


# ---------------------------------------------------------------------------
# 折线图
# ---------------------------------------------------------------------------
def test_line_time_series():
    r = _mk(
        ["ym", "total_amount"],
        [["2026-01", 100.0], ["2026-02", 120.0], ["2026-03", 90.0]],
    )
    c = select_chart(r, "月度销售额趋势")
    assert c.chart_type == "line"
    assert c.x == "ym"


def test_line_full_date_column():
    r = _mk(
        ["sale_date", "amount"],
        [["2026-01-01", 10.0], ["2026-01-02", 20.0]],
    )
    c = select_chart(r, "每日销售额")
    assert c.chart_type == "line"


# ---------------------------------------------------------------------------
# 柱状图 / 饼图
# ---------------------------------------------------------------------------
def test_bar_group_comparison():
    r = _mk(
        ["region_name", "total_amount"],
        [["华东", 100.0], ["华南", 80.0], ["华北", 60.0], ["西南", 30.0]],
    )
    c = select_chart(r, "各区域销售额")
    assert c.chart_type == "bar"


def test_pie_proportion():
    r = _mk(
        ["category", "total_amount"],
        [["压缩机", 40.0], ["真空设备", 30.0], ["液压件", 20.0], ["精密件", 10.0]],
    )
    c = select_chart(r, "各产品类别销售占比")
    assert c.chart_type == "pie"


def test_bar_too_many_categories():
    rows = [[f"产品{i}", float(i)] for i in range(1, 15)]  # 14 类 > PIE_MAX_CATEGORIES
    r = _mk(["product_name", "amount"], rows)
    c = select_chart(r, "各产品销售占比")
    assert c.chart_type == "bar"


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------
def test_table_detail_columns():
    r = _mk(
        ["product_name", "category", "unit_price"],
        [["螺杆式空气压缩机", "压缩机", 68000.0]],
    )
    c = select_chart(r, "产品明细")
    assert c.chart_type == "table"


def test_table_empty_result():
    r = _mk(["product_name", "amount"], [])
    c = select_chart(r, "查询")
    assert c.chart_type == "table"
