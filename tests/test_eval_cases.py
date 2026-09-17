# -*- coding: utf-8 -*-
"""评测集与结果比对逻辑的单元测试。

覆盖：
- 评测集完整性（条数、编号唯一、字段齐全、覆盖能力标签齐全）
- 参考答案可复现（用 reference_sql 在真实数仓执行，验证 expected 与之一致）
- 结果比对逻辑（scalar 命中/不命中、rows 有序/无序、数值容差）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb  # noqa: E402

from src import config  # noqa: E402
from src.eval_cases import (  # noqa: E402
    DEFAULT_TOLERANCE,
    EVAL_CASES,
    compare_rows,
    compare_scalar,
    evaluate_result,
    extract_pairs,
    extract_scalar,
)


# ---------------------------------------------------------------------------
# 评测集完整性
# ---------------------------------------------------------------------------
def test_eval_case_count_within_range():
    """评测集条数应在 15~20 之间。"""
    assert 15 <= len(EVAL_CASES) <= 20


def test_eval_case_ids_unique():
    """用例编号应唯一。"""
    ids = [c["id"] for c in EVAL_CASES]
    assert len(ids) == len(set(ids))


def test_eval_case_required_fields():
    """每条用例应包含问题、参考答案等必要字段。"""
    for c in EVAL_CASES:
        assert c["question"].strip(), f"{c['id']} 缺少问题"
        assert c["reference_sql"].strip(), f"{c['id']} 缺少参考答案 SQL"
        assert "expected" in c, f"{c['id']} 缺少 expected"
        assert c["expected"]["type"] in ("scalar", "rows"), f"{c['id']} 非法 expected.type"
        if c["expected"]["type"] == "rows":
            assert c["expected"]["rows"], f"{c['id']} rows 期望为空"


def test_eval_case_coverage():
    """评测集应覆盖要求的能力标签：求和/平均/同比环比、分组、排序、TopN、时间范围、条件过滤、多表关联。"""
    required = {
        "求和",
        "平均",
        "同比环比",
        "分组",
        "排序",
        "TopN",
        "时间范围",
        "条件过滤",
        "多表关联",
    }
    covered = {tag for c in EVAL_CASES for tag in c["categories"]}
    missing = required - covered
    assert not missing, f"缺少能力标签：{missing}"


# ---------------------------------------------------------------------------
# 参考答案可复现（用真实数仓验证 expected）
# ---------------------------------------------------------------------------
def _exec(reference_sql: str):
    """在真实数仓只读执行参考答案 SQL，返回 (columns, rows)。"""
    conn = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    try:
        cur = conn.execute(reference_sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        rows = [list(r) for r in rows]
        return columns, rows
    finally:
        conn.close()


def test_reference_sql_matches_expected():
    """每条用例的 expected 应与 reference_sql 执行结果一致（防参考答案漂移）。"""
    for c in EVAL_CASES:
        columns, rows = _exec(c["reference_sql"])
        ok = evaluate_result(columns, rows, c["expected"], DEFAULT_TOLERANCE)
        assert ok, (
            f"{c['id']} 参考答案与 reference_sql 不一致："
            f"sql 结果={rows[:5]}，expected={c['expected']}"
        )


# ---------------------------------------------------------------------------
# 结果比对逻辑
# ---------------------------------------------------------------------------
def test_extract_scalar_single_row():
    """单行结果应能提取数值。"""
    assert extract_scalar([[123.45]]) == 123.45
    assert extract_scalar([[92203175.0]]) == 92203175.0


def test_extract_scalar_invalid():
    """非单行或无数值时应返回 None。"""
    assert extract_scalar([]) is None
    assert extract_scalar([[1], [2]]) is None
    assert extract_scalar([["abc"]]) is None


def test_extract_pairs_label_value():
    """「标签列 + 数值列」结果应能提取为 (label, value) 对。"""
    cols = ["region_name", "total_amount"]
    rows = [["华东", 1.0], ["华南", 2.0]]
    assert extract_pairs(cols, rows) == [("华东", 1.0), ("华南", 2.0)]


def test_extract_pairs_value_first_column():
    """数值列在前时也应正确识别标签列与数值列。"""
    cols = ["total_amount", "region_name"]
    rows = [[1.0, "华东"], [2.0, "华南"]]
    assert extract_pairs(cols, rows) == [("华东", 1.0), ("华南", 2.0)]


def test_compare_scalar_hit_with_tolerance():
    """数值在容差内应判定命中。"""
    assert compare_scalar(100.0, 100.0, DEFAULT_TOLERANCE)
    # 1% 相对容差内
    assert compare_scalar(100.5, 100.0, DEFAULT_TOLERANCE)


def test_compare_scalar_miss():
    """数值超容差应判定未命中。"""
    assert not compare_scalar(200.0, 100.0, DEFAULT_TOLERANCE)


def test_compare_rows_unordered():
    """无序比对：顺序不同但集合一致应命中。"""
    actual = [("华南", 2.0), ("华东", 1.0)]
    expected = [["华东", 1.0], ["华南", 2.0]]
    assert compare_rows(actual, expected, ordered=False, tol=DEFAULT_TOLERANCE)


def test_compare_rows_ordered_hit():
    """有序比对：顺序一致应命中。"""
    actual = [("华东", 1.0), ("华南", 2.0)]
    expected = [["华东", 1.0], ["华南", 2.0]]
    assert compare_rows(actual, expected, ordered=True, tol=DEFAULT_TOLERANCE)


def test_compare_rows_ordered_miss_order():
    """有序比对：顺序不一致应未命中。"""
    actual = [("华南", 2.0), ("华东", 1.0)]
    expected = [["华东", 1.0], ["华南", 2.0]]
    assert not compare_rows(actual, expected, ordered=True, tol=DEFAULT_TOLERANCE)


def test_compare_rows_miss_count():
    """行数不一致应未命中。"""
    actual = [("华东", 1.0)]
    expected = [["华东", 1.0], ["华南", 2.0]]
    assert not compare_rows(actual, expected, ordered=False, tol=DEFAULT_TOLERANCE)


def test_compare_rows_miss_value():
    """标签一致但数值超容差应未命中。"""
    actual = [("华东", 99.0)]
    expected = [["华东", 1.0]]
    assert not compare_rows(actual, expected, ordered=False, tol=DEFAULT_TOLERANCE)


def test_evaluate_result_scalar_end_to_end():
    """端到端：scalar 期望 + 实际结果命中。"""
    assert evaluate_result(
        ["total_amount"], [[92203175.0]], {"type": "scalar", "value": 92203175.0}
    )


def test_evaluate_result_rows_end_to_end():
    """端到端：rows 期望 + 实际结果命中。"""
    expected = {
        "type": "rows",
        "ordered": True,
        "rows": [["华东", 1.0], ["华南", 2.0]],
    }
    assert evaluate_result(
        ["region_name", "total_amount"],
        [["华东", 1.0], ["华南", 2.0]],
        expected,
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
