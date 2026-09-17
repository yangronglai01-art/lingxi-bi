# -*- coding: utf-8 -*-
"""数据生成模块。

生成「宁波鲍斯能源装备」经营分析所需的 6 张表数据（3 维度 + 3 事实）。

设计要点（对应面试演示中的数据特征）：
1. **时间趋势**：整体销售额随月份温和增长（月度 +1.8%），并叠加季节性波动
   （11/12 月年末旺季、2 月春节低谷），便于演示同比/环比与增长趋势洞察。
2. **区域差异**：华东 > 华南 > 华北 > 西南（销量权重 0.42/0.28/0.20/0.10），
   便于演示区域对比（如「华东销售额明显高于西南」）。
3. **良率波动**：精密齿轮轴(P006) 在产线3 于 2026-03 ~ 2026-05 缺陷率阶段性
   偏高（约 9%~13%，正常约 2%~3%），同期产线3 停机概率同步升高，便于演示质量归因。
4. **逻辑自洽**：amount = quantity * unit_price 严格成立；cost 与 amount 保持
   合理毛利关系（毛利率约 26%~46%），便于演示毛利/毛利率分析。

全部随机性来源于固定种子的 `random.Random` 实例，保证数据可复现。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Tuple

import pandas as pd

from src import config


# ===========================================================================
# 基础业务字典
# ===========================================================================
@dataclass(frozen=True)
class Product:
    """产品定义。"""

    product_id: str      # 产品编码
    product_name: str    # 产品名称
    category: str        # 产品类别
    unit_price: float    # 单价（元）
    line: str            # 生产/检测产线
    base_demand: int     # 全区域合计的日均需求量（件）
    gross_margin: float  # 基准毛利率（cost = amount × (1 - margin)）


PRODUCTS: List[Product] = [
    Product("P001", "螺杆式空气压缩机", "压缩机",   6800.00, "产线1",  30, 0.30),
    Product("P002", "无油涡旋压缩机",   "压缩机",   4200.00, "产线1",  35, 0.33),
    Product("P003", "旋片式真空泵",     "真空设备", 2800.00, "产线2",  40, 0.35),
    Product("P004", "罗茨真空泵机组",   "真空设备", 9500.00, "产线2",  28, 0.32),
    Product("P005", "液压齿轮泵",       "液压件",    750.00, "产线3",  80, 0.38),
    Product("P006", "精密齿轮轴",       "精密件",    140.00, "产线3", 300, 0.42),
]


@dataclass(frozen=True)
class Region:
    """区域定义。"""

    region_id: str    # 区域编码
    region_name: str  # 区域名称
    province: str     # 区域中心省份
    weight: float     # 区域销量权重（合计约 1.0）


REGIONS: List[Region] = [
    Region("R001", "华东", "浙江", 0.42),
    Region("R002", "华南", "广东", 0.28),
    Region("R003", "华北", "北京", 0.20),
    Region("R004", "西南", "四川", 0.10),
]


@dataclass(frozen=True)
class Customer:
    """客户定义。"""

    customer_id: str    # 客户编码
    customer_name: str  # 客户名称
    tier: str           # 客户层级
    industry: str       # 所属行业
    region_id: str      # 所属区域
    weight: float       # 区域内采购份额权重（同区域内合计约 1.0）


CUSTOMERS: List[Customer] = [
    Customer("C001", "华东装备集团",   "大客户",   "设备制造商", "R001", 0.35),
    Customer("C002", "华南重工股份",   "大客户",   "设备制造商", "R002", 0.35),
    Customer("C003", "华北机械制造",   "大客户",   "设备制造商", "R003", 0.40),
    Customer("C004", "西南工业装备",   "大客户",   "设备制造商", "R004", 0.55),
    Customer("C005", "华东工业服务A",  "中小客户", "工业服务",   "R001", 0.12),
    Customer("C006", "华南工业服务B",  "中小客户", "工业服务",   "R002", 0.12),
    Customer("C007", "华东机电贸易",   "中小客户", "经销商",     "R001", 0.18),
    Customer("C008", "华南机电市场",   "中小客户", "经销商",     "R002", 0.18),
    Customer("C009", "华北工控经销",   "中小客户", "经销商",     "R003", 0.60),
    Customer("C010", "西南机电商贸",   "中小客户", "经销商",     "R004", 0.45),
    Customer("C011", "宁波出口贸易",   "大客户",   "出口贸易",   "R001", 0.35),
    Customer("C012", "华南出口贸易",   "大客户",   "出口贸易",   "R002", 0.35),
]

# 良率异常窗口：精密齿轮轴(P006) 在产线3 缺陷率阶段性偏高的时间段
QUALITY_ISSUE_START = date(2026, 3, 1)
QUALITY_ISSUE_END = date(2026, 5, 31)


# ===========================================================================
# 工具函数
# ===========================================================================
def _date_range(start: str, end: str) -> List[date]:
    """生成 [start, end] 闭区间内的日期列表。"""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out: List[date] = []
    d = s
    while d <= e:
        out.append(d)
        d += timedelta(days=1)
    return out


def _seasonal_factor(d: date) -> float:
    """按月份返回季节性系数（模拟真实行业淡旺季）。"""
    m = d.month
    if m == 2:
        return 0.78     # 春节月：需求低谷
    if m in (11, 12):
        return 1.12     # 年末旺季
    if m == 1:
        return 1.05     # 年初备货
    return 1.0


# ===========================================================================
# 维度表生成
# ===========================================================================
def generate_dim_product() -> pd.DataFrame:
    """生成产品维度表（6 行）。"""
    return pd.DataFrame([
        {
            "product_id": p.product_id,
            "product_name": p.product_name,
            "category": p.category,
            "unit_price": p.unit_price,
        }
        for p in PRODUCTS
    ])


def generate_dim_region() -> pd.DataFrame:
    """生成区域维度表（4 行）。"""
    return pd.DataFrame([
        {
            "region_id": r.region_id,
            "region_name": r.region_name,
            "province": r.province,
        }
        for r in REGIONS
    ])


def generate_dim_customer() -> pd.DataFrame:
    """生成客户维度表（12 行）。"""
    return pd.DataFrame([
        {
            "customer_id": c.customer_id,
            "customer_name": c.customer_name,
            "tier": c.tier,
            "industry": c.industry,
        }
        for c in CUSTOMERS
    ])


# ===========================================================================
# 事实表生成
# ===========================================================================
def generate_fact_sales(dates: List[date], rng: random.Random) -> pd.DataFrame:
    """生成销售事实表。

    规则：
    - 每日 × 产品 × 区域 计算期望需求，再按区域内客户权重拆分为 2~4 笔订单；
    - 需求 = 基础需求 × 区域权重 × 月度增长趋势 × 季节性 × 周末系数 × 随机波动；
    - amount = quantity × unit_price；cost 按（基准毛利率 ± 4%）浮动计算。
    """
    start = date.fromisoformat(config.START_DATE)
    rows: List[Tuple] = []

    for d in dates:
        # 月度增长趋势：以起始月为基准，逐月 +1.8%
        month_index = (d.year - start.year) * 12 + (d.month - start.month)
        trend = 1.0 + 0.018 * month_index
        seasonal = _seasonal_factor(d)
        weekday_factor = 0.65 if d.weekday() >= 5 else 1.0  # 周末需求较低

        for p in PRODUCTS:
            for r in REGIONS:
                demand = (p.base_demand * r.weight * trend * seasonal
                          * weekday_factor * rng.uniform(0.80, 1.20))
                if demand < 1.0:
                    continue  # 需求过小，该日该区域该产品无销售

                # 拆分为 2~4 笔客户订单
                region_customers = [c for c in CUSTOMERS if c.region_id == r.region_id]
                n_tx = min(rng.randint(2, 4), len(region_customers))
                chosen = rng.sample(region_customers, k=n_tx)
                w_sum = sum(c.weight for c in chosen)

                for c in chosen:
                    qty = max(1, int(round(
                        demand * (c.weight / w_sum) * rng.uniform(0.70, 1.30)
                    )))
                    amount = round(qty * p.unit_price, 2)
                    margin = min(0.60, max(0.10, p.gross_margin + rng.uniform(-0.04, 0.04)))
                    cost = round(amount * (1.0 - margin), 2)
                    rows.append((d, p.product_id, r.region_id, c.customer_id,
                                 qty, amount, cost))

    return pd.DataFrame(rows, columns=[
        "sale_date", "product_id", "region_id", "customer_id",
        "quantity", "amount", "cost",
    ])


def generate_fact_quality(dates: List[date], rng: random.Random) -> pd.DataFrame:
    """生成质量事实表。

    规则：
    - 精密零部件实行全检：抽检量 ≈ 当日产量（±10% 波动）；
    - 基础缺陷率 2%~3%；异常窗口内 P006/产线3 缺陷率升至 9%~13%。
    """
    rows: List[Tuple] = []

    for d in dates:
        for p in PRODUCTS:
            # 全检：抽检量 ≈ 当日产量（与生产事实表量级一致）
            checked = int(round(p.base_demand * rng.uniform(0.90, 1.10)))
            rate = rng.uniform(0.02, 0.03)

            # 良率波动：P006 在产线3 于异常窗口缺陷率阶段性偏高
            if (p.product_id == "P006" and p.line == "产线3"
                    and QUALITY_ISSUE_START <= d <= QUALITY_ISSUE_END):
                rate = rng.uniform(0.09, 0.13)

            # 二项抽样生成缺陷数：低产量产品也能得到符合真实缺陷率的计数
            defect = sum(1 for _ in range(checked) if rng.random() < rate)
            rows.append((d, p.product_id, p.line, checked, defect))

    return pd.DataFrame(rows, columns=[
        "check_date", "product_id", "line", "checked_count", "defect_count",
    ])


def generate_fact_production(dates: List[date], rng: random.Random) -> pd.DataFrame:
    """生成生产事实表。

    规则：
    - 计划产量 ≈ 日均需求量（±5%）；
    - 实际产量 = 计划 × 效率（93%~100%）- 停机损失；
    - 异常窗口内产线3（P006）停机概率升高（与良率波动联动，体现设备老化）。
    """
    rows: List[Tuple] = []

    for d in dates:
        for p in PRODUCTS:
            planned = int(round(p.base_demand * rng.uniform(0.95, 1.05)))

            issue = (p.product_id == "P006" and p.line == "产线3"
                     and QUALITY_ISSUE_START <= d <= QUALITY_ISSUE_END)
            downtime_prob = 0.18 if issue else 0.08
            downtime = 0.0
            if rng.random() < downtime_prob:
                downtime = round(rng.uniform(0.5, 4.0), 2)

            actual = int(round(planned * rng.uniform(0.93, 1.00)))
            if downtime > 0:
                # 每小时产能约 base_demand / 16（按两班 16 小时估算）
                loss_per_hour = max(1, int(round(p.base_demand / 16.0)))
                actual = max(0, actual - int(round(downtime * loss_per_hour)))

            rows.append((d, p.product_id, p.line, planned, actual, downtime))

    return pd.DataFrame(rows, columns=[
        "prod_date", "product_id", "line", "planned_qty", "actual_qty", "downtime_hours",
    ])


# ===========================================================================
# 汇总入口
# ===========================================================================
def generate_all() -> Dict[str, pd.DataFrame]:
    """生成全部 6 张表，保存 CSV 快照，并返回 {表名: DataFrame} 字典。"""
    rng = random.Random(config.RANDOM_SEED)
    dates = _date_range(config.START_DATE, config.END_DATE)

    dfs = {
        "dim_product": generate_dim_product(),
        "dim_region": generate_dim_region(),
        "dim_customer": generate_dim_customer(),
        "fact_sales": generate_fact_sales(dates, rng),
        "fact_quality": generate_fact_quality(dates, rng),
        "fact_production": generate_fact_production(dates, rng),
    }

    # 保存 CSV 快照（utf-8-sig 便于 Excel 直接打开中文）
    config.CSV_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in dfs.items():
        df.to_csv(config.CSV_DIR / f"{name}.csv", index=False, encoding="utf-8-sig")

    return dfs


if __name__ == "__main__":
    # 单独运行本模块：仅生成数据 + 打印各表行数
    result = generate_all()
    for name, df in result.items():
        print(f"{name:<16} {len(df):>8,} 行")
