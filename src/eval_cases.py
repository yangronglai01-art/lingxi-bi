# -*- coding: utf-8 -*-
"""评测集定义：18 条中文问数 + 参考答案 + 结果比对逻辑。

评测集是「宁波鲍斯能源装备」智能问数平台的离线评测基准，覆盖
求和 / 平均 / 同比环比、分组、排序、TopN、时间范围、条件过滤、多表关联
等 Text-to-SQL 常见能力。每条用例都基于 docs/schema.md 与真实数仓数据，
给出「有明确可验证答案」的参考答案（关键指标值），供 scripts/eval.py
批量评测时做数值容差 / 集合比对。

数据结构约定：
- 每条用例为 dict，字段：
    id          用例编号（E01..E18）
    question    中文问数
    categories  覆盖的能力标签（list[str]）
    reference_sql  参考答案 SQL（语义标准答案，可复现）
    expected    参考答案（关键指标值），type 取值：
                    "scalar" -> {"value": 数值}
                    "rows"   -> {"rows": [[label, value], ...], "ordered": bool}
    tolerance   数值比对容差（dict，含 rel 相对 / abs 绝对）
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 评测用例（参考答案来自真实数仓，见 reports/_reference_values.json）
# ---------------------------------------------------------------------------
EVAL_CASES: List[Dict[str, Any]] = [
    {
        "id": "E01",
        "question": "全年总销售额是多少？",
        "categories": ["求和"],
        "reference_sql": "SELECT ROUND(SUM(amount), 2) AS total_amount FROM fact_sales",
        "expected": {"type": "scalar", "value": 92203175.0},
    },
    {
        "id": "E02",
        "question": "压缩机类产品的销售额是多少？",
        "categories": ["求和", "条件过滤", "多表关联"],
        "reference_sql": (
            "SELECT ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_product p ON s.product_id = p.product_id WHERE p.category = '压缩机'"
        ),
        "expected": {"type": "scalar", "value": 41434875.0},
    },
    {
        "id": "E03",
        "question": "平均每天的销售额是多少？",
        "categories": ["平均"],
        "reference_sql": (
            "SELECT ROUND(SUM(amount) / COUNT(DISTINCT sale_date), 2) "
            "AS avg_daily_amount FROM fact_sales"
        ),
        "expected": {"type": "scalar", "value": 252611.44},
    },
    {
        "id": "E04",
        "question": "各区域的销售额分别是多少？",
        "categories": ["分组", "多表关联"],
        "reference_sql": (
            "SELECT r.region_name, ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_region r ON s.region_id = r.region_id GROUP BY r.region_name"
        ),
        "expected": {
            "type": "rows",
            "ordered": False,
            "rows": [
                ["华东", 38740955.0],
                ["华南", 25821435.0],
                ["华北", 18491920.0],
                ["西南", 9148865.0],
            ],
        },
    },
    {
        "id": "E05",
        "question": "各产品类别的销售额分别是多少？",
        "categories": ["分组", "多表关联"],
        "reference_sql": (
            "SELECT p.category, ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_product p ON s.product_id = p.product_id GROUP BY p.category"
        ),
        "expected": {
            "type": "rows",
            "ordered": False,
            "rows": [
                ["制动", 41434875.0],
                ["传动", 29405360.0],
                ["电子件", 13157640.0],
                ["密封", 8205300.0],
            ],
        },
    },
    {
        "id": "E06",
        "question": "销售额最高的5个产品是哪些？",
        "categories": ["排序", "TopN", "多表关联"],
        "reference_sql": (
            "SELECT p.product_name, ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_product p ON s.product_id = p.product_id GROUP BY p.product_name "
            "ORDER BY SUM(s.amount) DESC LIMIT 5"
        ),
        "expected": {
            "type": "rows",
            "ordered": True,
            "rows": [
                ["陶瓷刹车片", 24853575.0],
                ["盘式制动器总成", 16581300.0],
                ["传动轴万向节", 15876960.0],
                ["变速箱同步器总成", 13528400.0],
                ["轮速传感器", 13157640.0],
            ],
        },
    },
    {
        "id": "E07",
        "question": "2026年上半年每个月的销售额趋势？",
        "categories": ["时间范围", "分组"],
        "reference_sql": (
            "SELECT strftime(s.sale_date, '%Y-%m') AS ym, ROUND(SUM(s.amount), 2) "
            "FROM fact_sales s WHERE s.sale_date BETWEEN '2026-01-01' AND '2026-06-30' "
            "GROUP BY ym ORDER BY ym"
        ),
        "expected": {
            "type": "rows",
            "ordered": True,
            "rows": [
                ["2026-01", 7893160.0],
                ["2026-02", 5410860.0],
                ["2026-03", 7849240.0],
                ["2026-04", 7746090.0],
                ["2026-05", 7967290.0],
                ["2026-06", 7965160.0],
            ],
        },
    },
    {
        "id": "E08",
        "question": "2026年2月和3月的销售额分别是多少（用于环比对比）？",
        "categories": ["同比环比", "时间范围"],
        "reference_sql": (
            "SELECT strftime(s.sale_date, '%Y-%m') AS ym, ROUND(SUM(s.amount), 2) "
            "FROM fact_sales s WHERE strftime(s.sale_date, '%Y-%m') IN ('2026-02', '2026-03') "
            "GROUP BY ym ORDER BY ym"
        ),
        "expected": {
            "type": "rows",
            "ordered": True,
            "rows": [["2026-02", 5410860.0], ["2026-03", 7849240.0]],
        },
    },
    {
        "id": "E09",
        "question": "各产品的缺陷率是多少？",
        "categories": ["分组", "多表关联"],
        "reference_sql": (
            "SELECT p.product_name, ROUND(SUM(q.defect_count) * 100.0 / SUM(q.checked_count), 2) "
            "FROM fact_quality q JOIN dim_product p ON q.product_id = p.product_id "
            "GROUP BY p.product_name"
        ),
        "expected": {
            "type": "rows",
            "ordered": False,
            "rows": [
                ["轮速传感器", 4.58],
                ["盘式制动器总成", 2.51],
                ["传动轴万向节", 2.51],
                ["发动机密封垫片", 2.50],
                ["陶瓷刹车片", 2.49],
                ["变速箱同步器总成", 2.43],
            ],
        },
    },
    {
        "id": "E10",
        "question": "华东地区的销售额是多少？",
        "categories": ["条件过滤", "多表关联"],
        "reference_sql": (
            "SELECT ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_region r ON s.region_id = r.region_id WHERE r.region_name = '华东'"
        ),
        "expected": {"type": "scalar", "value": 38740955.0},
    },
    {
        "id": "E11",
        "question": "整体毛利率是多少？",
        "categories": ["求和"],
        "reference_sql": (
            "SELECT ROUND((SUM(amount) - SUM(cost)) * 100.0 / SUM(amount), 2) "
            "AS gross_margin_rate FROM fact_sales"
        ),
        "expected": {"type": "scalar", "value": 35.30},
    },
    {
        "id": "E12",
        "question": "各区域的销售数量分别是多少？",
        "categories": ["分组", "多表关联"],
        "reference_sql": (
            "SELECT r.region_name, SUM(s.quantity) FROM fact_sales s "
            "JOIN dim_region r ON s.region_id = r.region_id GROUP BY r.region_name"
        ),
        "expected": {
            "type": "rows",
            "ordered": False,
            "rows": [
                ["华东", 300926.0],
                ["华南", 200486.0],
                ["华北", 143691.0],
                ["西南", 70888.0],
            ],
        },
    },
    {
        "id": "E13",
        "question": "销售额最高的5个客户是哪些？",
        "categories": ["排序", "TopN", "多表关联"],
        "reference_sql": (
            "SELECT c.customer_name, ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_customer c ON s.customer_id = c.customer_id GROUP BY c.customer_name "
            "ORDER BY SUM(s.amount) DESC LIMIT 5"
        ),
        "expected": {
            "type": "rows",
            "ordered": True,
            "rows": [
                ["华东汽车集团", 13260380.0],
                ["恒跃出口贸易", 12747475.0],
                ["华北配件经销", 11098945.0],
                ["南方出口贸易", 8677700.0],
                ["南方客车制造", 8608140.0],
            ],
        },
    },
    {
        "id": "E14",
        "question": "2026年1月的销售额是多少？",
        "categories": ["时间范围", "条件过滤"],
        "reference_sql": (
            "SELECT ROUND(SUM(amount), 2) FROM fact_sales "
            "WHERE strftime(sale_date, '%Y-%m') = '2026-01'"
        ),
        "expected": {"type": "scalar", "value": 7893160.0},
    },
    {
        "id": "E15",
        "question": "2026年上半年每个月的产量达成率？",
        "categories": ["时间范围", "分组", "多表关联"],
        "reference_sql": (
            "SELECT strftime(prod_date, '%Y-%m') AS ym, "
            "ROUND(SUM(actual_qty) * 100.0 / SUM(planned_qty), 2) FROM fact_production "
            "WHERE prod_date BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY ym ORDER BY ym"
        ),
        "expected": {
            "type": "rows",
            "ordered": True,
            "rows": [
                ["2026-01", 95.34],
                ["2026-02", 94.97],
                ["2026-03", 94.82],
                ["2026-04", 94.75],
                ["2026-05", 95.68],
                ["2026-06", 94.91],
            ],
        },
    },
    {
        "id": "E16",
        "question": "精密件类产品的销售额是多少？",
        "categories": ["条件过滤", "多表关联"],
        "reference_sql": (
            "SELECT ROUND(SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_product p ON s.product_id = p.product_id WHERE p.category = '精密件'"
        ),
        "expected": {"type": "scalar", "value": 13157640.0},
    },
    {
        "id": "E17",
        "question": "单笔销售金额最高是多少？",
        "categories": ["排序"],
        "reference_sql": "SELECT MAX(amount) FROM fact_sales",
        "expected": {"type": "scalar", "value": 34340.0},
    },
    {
        "id": "E18",
        "question": "大客户的销售额占总销售额的比例是多少？",
        "categories": ["条件过滤", "多表关联", "求和"],
        "reference_sql": (
            "SELECT ROUND(SUM(CASE WHEN c.tier = '大客户' THEN s.amount END) * 100.0 "
            "/ SUM(s.amount), 2) FROM fact_sales s "
            "JOIN dim_customer c ON s.customer_id = c.customer_id"
        ),
        "expected": {"type": "scalar", "value": 60.44},
    },
]

# 默认数值容差：相对 1%（防止浮点尾差），绝对 0.01
DEFAULT_TOLERANCE: Dict[str, float] = {"rel": 0.01, "abs": 0.01}


# ---------------------------------------------------------------------------
# 结果比对
# ---------------------------------------------------------------------------
def _is_number(v: Any) -> bool:
    """判断是否为数值（含 int/float/Decimal，排除 bool）。"""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float, Decimal))


def _to_float(v: Any) -> float:
    """把 int/float/Decimal 统一转为 float（用于容差比对）。"""
    return float(v)


def _close(actual: float, expected: float, tol: Dict[str, float]) -> bool:
    """按相对 + 绝对容差判断两个数值是否接近。"""
    rel = tol.get("rel", DEFAULT_TOLERANCE["rel"])
    abs_tol = tol.get("abs", DEFAULT_TOLERANCE["abs"])
    return abs(actual - expected) <= (abs_tol + rel * abs(expected))


def extract_scalar(rows: List[List[Any]]) -> Optional[float]:
    """从单行结果中提取唯一数值。

    参数:
        rows: 查询结果二维列表。

    返回:
        数值（找不到返回 None）。
    """
    if not rows or len(rows) != 1:
        return None
    for v in rows[0]:
        if _is_number(v):
            return _to_float(v)
    return None


def extract_pairs(
    columns: List[str], rows: List[List[Any]]
) -> Optional[List[Tuple[str, float]]]:
    """把「标签列 + 数值列」的结果规整为 (label, value) 对列表。

    参数:
        columns: 列名列表。
        rows: 行数据二维列表。

    返回:
        (label, value) 对列表；无法识别数值列时返回 None。
    """
    if not rows or len(columns) < 2:
        return None

    # 找到数值列：整列均为数值的列
    value_col: Optional[int] = None
    for j in range(len(columns)):
        col_vals = [r[j] for r in rows if j < len(r)]
        if col_vals and all(_is_number(v) for v in col_vals):
            value_col = j
            break
    if value_col is None:
        return None

    label_col = 0 if value_col != 0 else 1
    pairs: List[Tuple[str, float]] = []
    for r in rows:
        if label_col >= len(r) or value_col >= len(r):
            return None
        val = r[value_col]
        if not _is_number(val):
            return None
        pairs.append((str(r[label_col]), _to_float(val)))
    return pairs


def compare_scalar(actual: float, expected: float, tol: Dict[str, float]) -> bool:
    """比对单个数值（容差内视为命中）。"""
    return _close(actual, expected, tol)


def compare_rows(
    actual_pairs: List[Tuple[str, float]],
    expected_rows: List[List[Any]],
    ordered: bool,
    tol: Dict[str, float],
) -> bool:
    """比对分组 / TopN / 时间序列结果。

    - ordered=False：按「标签集合 + 每个标签的数值」比对（忽略顺序）；
    - ordered=True ：额外要求标签顺序一致（用于 TopN / 时间序列）。
    """
    expected_pairs = [(str(label), float(val)) for label, val in expected_rows]

    if len(actual_pairs) != len(expected_pairs):
        return False

    # 数值逐项比对（先不管顺序）
    def match_pairs(actual: List[Tuple[str, float]], expected: List[Tuple[str, float]]) -> bool:
        if ordered:
            # 顺序必须一致
            for (al, av), (el, ev) in zip(actual, expected):
                if al != el or not _close(av, ev, tol):
                    return False
            return True
        # 无序：逐个在期望集中找匹配（避免重复标签误配）
        remaining = list(expected)
        for al, av in actual:
            matched = False
            for i, (el, ev) in enumerate(remaining):
                if al == el and _close(av, ev, tol):
                    del remaining[i]
                    matched = True
                    break
            if not matched:
                return False
        return True

    return match_pairs(actual_pairs, expected_pairs)


def evaluate_result(
    columns: List[str],
    rows: List[List[Any]],
    expected: Dict[str, Any],
    tolerance: Optional[Dict[str, float]] = None,
) -> bool:
    """按期望类型评估一次查询结果是否命中参考答案。

    参数:
        columns: 实际结果列名。
        rows: 实际结果行数据。
        expected: 期望答案（type=scalar / rows）。
        tolerance: 容差配置，缺省用 DEFAULT_TOLERANCE。

    返回:
        是否命中（True / False）。
    """
    tol = tolerance or DEFAULT_TOLERANCE
    etype = expected["type"]

    if etype == "scalar":
        actual = extract_scalar(rows)
        if actual is None:
            return False
        return compare_scalar(actual, float(expected["value"]), tol)

    if etype == "rows":
        actual_pairs = extract_pairs(columns, rows)
        if actual_pairs is None:
            return False
        return compare_rows(
            actual_pairs,
            expected["rows"],
            bool(expected.get("ordered", False)),
            tol,
        )

    return False


if __name__ == "__main__":
    # 直接运行可自检：打印每条用例的问题与参考答案
    for c in EVAL_CASES:
        print(f"{c['id']}｜{c['question']}｜{c['expected']}")
