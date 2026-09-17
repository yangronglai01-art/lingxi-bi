# -*- coding: utf-8 -*-
"""Text-to-SQL 的 few-shot 示例集（中文问题 → DuckDB SQL）。

覆盖：求和、平均、同比/环比、分组、排序、TopN、时间范围、条件过滤、多表关联，
以及质量 / 生产 / 毛利等跨事实表的业务口径，帮助模型理解本项目表结构与 SQL 风格。
所有 SQL 均为只读 SELECT，且只使用 src/schema.py 中声明的表和字段。
"""

from __future__ import annotations

from typing import Dict, List

# 每条示例：{"question": 中文问题, "sql": 目标 SQL}
FEW_SHOTS: List[Dict[str, str]] = [
    {
        "question": "全年总销售额是多少？",
        "sql": "SELECT ROUND(SUM(amount), 2) AS total_amount FROM fact_sales",
    },
    {
        "question": "各区域的销售额分别是多少？",
        "sql": (
            "SELECT r.region_name, ROUND(SUM(s.amount), 2) AS total_amount "
            "FROM fact_sales s JOIN dim_region r ON s.region_id = r.region_id "
            "GROUP BY r.region_name ORDER BY total_amount DESC"
        ),
    },
    {
        "question": "2026年上半年每个月的销售额趋势？",
        "sql": (
            "SELECT strftime(s.sale_date, '%Y-%m') AS ym, ROUND(SUM(s.amount), 2) AS total_amount "
            "FROM fact_sales s WHERE s.sale_date BETWEEN '2026-01-01' AND '2026-06-30' "
            "GROUP BY ym ORDER BY ym"
        ),
    },
    {
        "question": "销售额最高的5个产品是哪些？",
        "sql": (
            "SELECT p.product_name, ROUND(SUM(s.amount), 2) AS total_amount "
            "FROM fact_sales s JOIN dim_product p ON s.product_id = p.product_id "
            "GROUP BY p.product_name ORDER BY total_amount DESC LIMIT 5"
        ),
    },
    {
        "question": "平均每天的销售额是多少？",
        "sql": "SELECT ROUND(SUM(amount) / COUNT(DISTINCT sale_date), 2) AS avg_daily_amount FROM fact_sales",
    },
    {
        "question": "压缩机类产品的销售额是多少？",
        "sql": (
            "SELECT ROUND(SUM(s.amount), 2) AS total_amount "
            "FROM fact_sales s JOIN dim_product p ON s.product_id = p.product_id "
            "WHERE p.category = '压缩机'"
        ),
    },
    {
        "question": "2026年2月和3月的销售额分别是多少（用于环比对比）？",
        "sql": (
            "SELECT strftime(s.sale_date, '%Y-%m') AS ym, ROUND(SUM(s.amount), 2) AS total_amount "
            "FROM fact_sales s WHERE strftime(s.sale_date, '%Y-%m') IN ('2026-02', '2026-03') "
            "GROUP BY ym ORDER BY ym"
        ),
    },
    {
        "question": "各产品的缺陷率是多少？",
        "sql": (
            "SELECT p.product_name, ROUND(SUM(q.defect_count) * 100.0 / SUM(q.checked_count), 2) AS defect_rate "
            "FROM fact_quality q JOIN dim_product p ON q.product_id = p.product_id "
            "GROUP BY p.product_name ORDER BY defect_rate DESC"
        ),
    },
]
