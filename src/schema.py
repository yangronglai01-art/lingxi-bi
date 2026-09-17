# -*- coding: utf-8 -*-
"""数据仓库 Schema 定义模块（单一数据源）。

本模块以结构化的方式定义 6 张表的字段清单（字段名、类型、中文说明、示例值、
是否可空、主键），并据此自动生成：
1. DuckDB 建表 DDL（含中文注释）；
2. 供 Text-to-SQL 使用的 schema 中文说明文档（docs/schema.md 的字段部分据此维护）。

后续任何阶段（Text-to-SQL、FastAPI、Streamlit）都应把本模块作为 schema 的唯一来源，
避免字段口径漂移。

6 张表（星型模型）：
- 维度表：dim_product（产品）、dim_region（区域）、dim_customer（客户）
- 事实表：fact_sales（销售）、fact_quality（质量）、fact_production（生产）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Column:
    """表字段定义。"""

    name: str            # 字段名
    dtype: str           # DuckDB 类型（VARCHAR / INTEGER / DECIMAL(10,2) / DATE ...）
    description: str     # 字段中文说明
    example: str         # 示例值（字符串形式，供文档展示）
    nullable: bool = False      # 是否允许为空（事实表外键默认非空）
    primary_key: bool = False   # 是否为主键


@dataclass(frozen=True)
class Table:
    """表定义。"""

    name: str              # 表名
    comment: str           # 表中文说明
    columns: List[Column]  # 字段列表（顺序即建表列顺序）


# ===========================================================================
# 表定义
# ===========================================================================
TABLES: dict = {
    # ------------------------------------------------------------------ 维度表
    "dim_product": Table(
        name="dim_product",
        comment="产品维度表：宁波鲍斯能源装备 6 款核心产品",
        columns=[
            Column("product_id", "VARCHAR", "产品编码（主键）", "P001", primary_key=True),
            Column("product_name", "VARCHAR", "产品名称", "螺杆式空气压缩机"),
            Column("category", "VARCHAR", "产品类别（压缩机/真空设备/液压件/精密件）", "压缩机"),
            Column("unit_price", "DECIMAL(10,2)", "销售单价（元）", "6800.00"),
        ],
    ),
    "dim_region": Table(
        name="dim_region",
        comment="区域维度表：四大销售区域",
        columns=[
            Column("region_id", "VARCHAR", "区域编码（主键）", "R001", primary_key=True),
            Column("region_name", "VARCHAR", "区域名称（华东/华南/华北/西南）", "华东"),
            Column("province", "VARCHAR", "区域中心省份", "浙江"),
        ],
    ),
    "dim_customer": Table(
        name="dim_customer",
        comment="客户维度表：设备制造商 / 工业服务 / 经销商 / 出口等客户",
        columns=[
            Column("customer_id", "VARCHAR", "客户编码（主键）", "C001", primary_key=True),
            Column("customer_name", "VARCHAR", "客户名称", "华东装备集团"),
            Column("tier", "VARCHAR", "客户层级（大客户/中小客户）", "大客户"),
            Column("industry", "VARCHAR", "所属行业（设备制造商/工业服务/经销商/出口贸易）", "设备制造商"),
        ],
    ),
    # ------------------------------------------------------------------ 事实表
    "fact_sales": Table(
        name="fact_sales",
        comment="销售事实表：逐日销售明细（金额逻辑：amount = quantity * unit_price）",
        columns=[
            Column("sale_date", "DATE", "销售日期", "2026-03-15"),
            Column("product_id", "VARCHAR", "产品编码（外键→dim_product）", "P001"),
            Column("region_id", "VARCHAR", "区域编码（外键→dim_region）", "R001"),
            Column("customer_id", "VARCHAR", "客户编码（外键→dim_customer）", "C001"),
            Column("quantity", "INTEGER", "销售数量（件）", "30"),
            Column("amount", "DECIMAL(12,2)", "销售额（元）= 数量 × 单价", "204000.00"),
            Column("cost", "DECIMAL(12,2)", "销售成本（元）", "142800.00"),
        ],
    ),
    "fact_quality": Table(
        name="fact_quality",
        comment="质量事实表：逐日抽检结果（缺陷率 = defect_count / checked_count）",
        columns=[
            Column("check_date", "DATE", "抽检日期", "2026-03-15"),
            Column("product_id", "VARCHAR", "产品编码（外键→dim_product）", "P006"),
            Column("line", "VARCHAR", "生产/检测产线（产线1/产线2/产线3）", "产线3"),
            Column("checked_count", "INTEGER", "抽检数量（件）", "30"),
            Column("defect_count", "INTEGER", "不合格数量（件）", "3"),
        ],
    ),
    "fact_production": Table(
        name="fact_production",
        comment="生产事实表：逐日生产计划与达成（达成率 = actual_qty / planned_qty）",
        columns=[
            Column("prod_date", "DATE", "生产日期", "2026-03-15"),
            Column("product_id", "VARCHAR", "产品编码（外键→dim_product）", "P006"),
            Column("line", "VARCHAR", "生产产线（产线1/产线2/产线3）", "产线3"),
            Column("planned_qty", "INTEGER", "计划产量（件）", "300"),
            Column("actual_qty", "INTEGER", "实际产量（件）", "285"),
            Column("downtime_hours", "DECIMAL(6,2)", "停机时长（小时）", "2.50"),
        ],
    ),
}

# 事实表外键 → 维度表主键的引用关系（用于文档与后续校验）
FOREIGN_KEYS: dict = {
    "fact_sales": {
        "product_id": "dim_product.product_id",
        "region_id": "dim_region.region_id",
        "customer_id": "dim_customer.customer_id",
    },
    "fact_quality": {"product_id": "dim_product.product_id"},
    "fact_production": {"product_id": "dim_product.product_id"},
}


def build_ddl(table: Table) -> str:
    """根据表定义生成 DuckDB 建表 DDL（含中文注释）。

    参数:
        table: Table 定义对象。

    返回:
        可直接执行的 CREATE TABLE 语句字符串。
    """
    lines: List[str] = [f"-- {table.comment}"]
    lines.append(f"CREATE TABLE IF NOT EXISTS {table.name} (")

    col_lines: List[str] = []
    for c in table.columns:
        # 字段中文说明独占一行，避免行内注释吞掉分隔逗号
        definition = f"  {c.name} {c.dtype}"
        if c.primary_key:
            definition += " PRIMARY KEY"
        elif not c.nullable:
            definition += " NOT NULL"
        col_lines.append(f"  -- {c.description}（示例：{c.example}）\n{definition}")

    lines.append(",\n".join(col_lines))
    lines.append(");")
    return "\n".join(lines)


def all_ddl() -> str:
    """生成全部 6 张表的建表 DDL。"""
    return "\n\n".join(build_ddl(t) for t in TABLES.values())


def render_markdown() -> str:
    """将 schema 渲染为 Markdown 表格（供文档 / Text-to-SQL prompt 复用）。"""
    md: List[str] = []
    for table in TABLES.values():
        md.append(f"### {table.name} — {table.comment}\n")
        md.append("| 字段 | 类型 | 说明 | 示例值 |")
        md.append("| --- | --- | --- | --- |")
        for c in table.columns:
            key = " 🔑主键" if c.primary_key else ""
            md.append(f"| `{c.name}`{key} | {c.dtype} | {c.description} | {c.example} |")
        md.append("")
    return "\n".join(md)


if __name__ == "__main__":
    # 直接运行本模块可预览 DDL 与 Markdown 文档
    print(all_ddl())
    print("\n\n" + "=" * 60 + "\n\n")
    print(render_markdown())
