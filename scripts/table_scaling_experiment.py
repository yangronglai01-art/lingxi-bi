# -*- coding: utf-8 -*-
"""表数量扩展压力测试（实测 Text-to-SQL 的「表推理上限」）。

实验目的：
    在 schema 中注入 N-6 张「干扰表」（其他业务域的真实风格表），
    跑同一套 18 条评测集，量化「表数量」对 SQL 生成正确性的影响，
    记录「表数量 → SQL 执行成功率 / 结果准确率 / 平均延迟 / schema 文本长度」。

方法：
    - 基准（N=6）为项目原生 6 张表；
    - 依次注入干扰表，使总表数达到 10 / 15 / 20 / 30 / 50；
    - 每个 N 值下，逐条跑 18 条评测（真实 LLM，temperature=0），
      流程与引擎一致：生成 SQL → 白名单校验 → 执行（失败自动修正一次）→ 结果比对；
    - 只测 SQL 正确性，不生成 AI 洞察（隔离变量、省一半调用）。

输出：
    - reports/table_scaling_experiment.json（完整明细）
    - reports/table_scaling_experiment.md（汇总表格）

注意：
    - 干扰表会临时加入 schema.TABLES 与 sql_guard 白名单，运行结束后自动还原，
      不影响项目原状态。
    - 需要 .env 已配置真实 API_KEY（否则走 mock，无法反映 LLM 退化）。
"""

from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, schema  # noqa: E402
from src.engine import executor, sql_guard, text2sql  # noqa: E402
from src.engine.executor import ExecutionError  # noqa: E402
from src.eval_cases import DEFAULT_TOLERANCE, EVAL_CASES, evaluate_result  # noqa: E402

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"

# 目标表数量（含基准 6 张）
TABLE_COUNTS = [6, 10, 15, 20, 30, 50]

# ---------------------------------------------------------------------------
# 干扰表池：真实企业其他业务域的表（HR / 财务 / 采购 / 物流 / 设备 / 市场 / 售后 / 研发 / 行政 / IT 等）
# 每项：(表名, 中文说明, [(字段名, 类型, 中文说明, 示例值), ...])
# ---------------------------------------------------------------------------
_DISTRACTOR_SPEC: List[tuple] = [
    # ---- 人力 HR ----
    ("dim_employee", "员工维度表：全公司员工档案（人力资源域）", [
        ("employee_id", "VARCHAR", "员工工号", "E1001"),
        ("employee_name", "VARCHAR", "员工姓名", "张三"),
        ("department", "VARCHAR", "所属部门", "财务部"),
        ("hire_date", "DATE", "入职日期", "2021-04-01"),
        ("salary", "DECIMAL(10,2)", "月薪（元）", "12500.00"),
    ]),
    ("dim_department", "部门维度表：组织架构（人力资源域）", [
        ("dept_id", "VARCHAR", "部门编码", "D01"),
        ("dept_name", "VARCHAR", "部门名称", "财务部"),
        ("manager_name", "VARCHAR", "负责人姓名", "李四"),
        ("headcount", "INTEGER", "部门人数", "18"),
    ]),
    ("fact_attendance", "考勤事实表：逐日考勤记录（人力资源域）", [
        ("att_date", "DATE", "考勤日期", "2026-03-15"),
        ("employee_id", "VARCHAR", "员工工号", "E1001"),
        ("work_hours", "DECIMAL(5,2)", "出勤工时", "8.00"),
        ("leave_hours", "DECIMAL(5,2)", "请假工时", "1.50"),
    ]),
    ("fact_payroll", "薪酬事实表：逐月发薪记录（人力资源域）", [
        ("pay_month", "VARCHAR", "发薪月份", "2026-03"),
        ("employee_id", "VARCHAR", "员工工号", "E1001"),
        ("gross_pay", "DECIMAL(10,2)", "应发工资（元）", "12500.00"),
        ("social_insurance", "DECIMAL(10,2)", "社保公积金（元）", "2600.00"),
    ]),
    # ---- 财务 Finance ----
    ("dim_account", "会计科目维度表：总账科目（财务域）", [
        ("account_id", "VARCHAR", "科目编码", "6602"),
        ("account_name", "VARCHAR", "科目名称", "管理费用"),
        ("account_type", "VARCHAR", "科目类别（资产/负债/损益）", "损益"),
    ]),
    ("fact_ledger", "总账事实表：逐笔会计分录（财务域）", [
        ("voucher_date", "DATE", "记账日期", "2026-03-15"),
        ("account_id", "VARCHAR", "科目编码", "6602"),
        ("debit_amount", "DECIMAL(12,2)", "借方金额（元）", "5000.00"),
        ("credit_amount", "DECIMAL(12,2)", "贷方金额（元）", "0.00"),
    ]),
    ("fact_budget", "预算事实表：逐月预算执行（财务域）", [
        ("budget_month", "VARCHAR", "预算月份", "2026-03"),
        ("dept_id", "VARCHAR", "部门编码", "D01"),
        ("budget_amount", "DECIMAL(12,2)", "预算金额（元）", "200000.00"),
        ("actual_amount", "DECIMAL(12,2)", "实际发生（元）", "185000.00"),
    ]),
    ("fact_expense", "费用报销事实表：逐笔报销（财务域）", [
        ("expense_date", "DATE", "报销日期", "2026-03-15"),
        ("employee_id", "VARCHAR", "员工工号", "E1001"),
        ("expense_type", "VARCHAR", "费用类型（差旅/办公/招待）", "差旅"),
        ("expense_amount", "DECIMAL(10,2)", "报销金额（元）", "860.00"),
    ]),
    # ---- 采购 Procurement ----
    ("dim_supplier", "供应商维度表：供应商档案（采购域）", [
        ("supplier_id", "VARCHAR", "供应商编码", "S001"),
        ("supplier_name", "VARCHAR", "供应商名称", "华东钢材集团"),
        ("material_type", "VARCHAR", "供应物料类型", "钢材"),
        ("credit_rating", "VARCHAR", "信用评级", "AA"),
    ]),
    ("fact_purchase_order", "采购订单事实表：逐单采购（采购域）", [
        ("order_date", "DATE", "下单日期", "2026-03-15"),
        ("supplier_id", "VARCHAR", "供应商编码", "S001"),
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("order_qty", "INTEGER", "采购数量", "500"),
        ("order_amount", "DECIMAL(12,2)", "采购金额（元）", "120000.00"),
    ]),
    ("fact_purchase_item", "采购明细事实表：逐行明细（采购域）", [
        ("item_date", "DATE", "明细日期", "2026-03-15"),
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("unit_price", "DECIMAL(10,2)", "采购单价（元）", "240.00"),
        ("item_qty", "INTEGER", "明细数量", "500"),
    ]),
    # ---- 物流/库存 Logistics ----
    ("dim_warehouse", "仓库维度表：仓库档案（物流域）", [
        ("warehouse_id", "VARCHAR", "仓库编码", "W001"),
        ("warehouse_name", "VARCHAR", "仓库名称", "宁波中心仓"),
        ("location", "VARCHAR", "仓库所在地", "浙江宁波"),
        ("capacity", "INTEGER", "库容（件）", "100000"),
    ]),
    ("fact_inventory", "库存事实表：逐日库存（物流域）", [
        ("inv_date", "DATE", "库存日期", "2026-03-15"),
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("warehouse_id", "VARCHAR", "仓库编码", "W001"),
        ("stock_qty", "INTEGER", "结存数量", "3200"),
    ]),
    ("fact_shipment", "发货事实表：逐单发货（物流域）", [
        ("ship_date", "DATE", "发货日期", "2026-03-15"),
        ("order_id", "VARCHAR", "订单号", "SO20260315001"),
        ("carrier", "VARCHAR", "承运商", "顺丰"),
        ("freight_cost", "DECIMAL(10,2)", "运费（元）", "680.00"),
    ]),
    ("fact_return_order", "退货事实表：逐单退货（物流域）", [
        ("return_date", "DATE", "退货日期", "2026-03-15"),
        ("order_id", "VARCHAR", "原订单号", "SO20260228001"),
        ("return_qty", "INTEGER", "退货数量", "2"),
        ("return_reason", "VARCHAR", "退货原因", "质量问题"),
    ]),
    # ---- 设备/维护 Maintenance ----
    ("dim_equipment", "设备维度表：设备台账（设备域）", [
        ("equipment_id", "VARCHAR", "设备编码", "EQ001"),
        ("equipment_name", "VARCHAR", "设备名称", "数控加工中心"),
        ("line", "VARCHAR", "所属产线", "产线1"),
        ("install_date", "DATE", "投产日期", "2020-06-01"),
    ]),
    ("fact_maintenance", "维修事实表：逐次维修（设备域）", [
        ("repair_date", "DATE", "维修日期", "2026-03-15"),
        ("equipment_id", "VARCHAR", "设备编码", "EQ001"),
        ("repair_hours", "DECIMAL(6,2)", "维修时长（小时）", "3.50"),
        ("repair_cost", "DECIMAL(10,2)", "维修费用（元）", "2600.00"),
    ]),
    ("fact_energy_consumption", "能耗事实表：逐日能耗（设备域）", [
        ("energy_date", "DATE", "记录日期", "2026-03-15"),
        ("line", "VARCHAR", "产线", "产线1"),
        ("electricity_kwh", "DECIMAL(12,2)", "用电量（千瓦时）", "1200.00"),
        ("water_ton", "DECIMAL(10,2)", "用水量（吨）", "8.50"),
    ]),
    # ---- 市场 Marketing ----
    ("dim_channel", "渠道维度表：销售渠道（市场域）", [
        ("channel_id", "VARCHAR", "渠道编码", "CH01"),
        ("channel_name", "VARCHAR", "渠道名称", "线上电商"),
        ("channel_type", "VARCHAR", "渠道类型（直销/经销/电商）", "电商"),
    ]),
    ("fact_campaign", "营销活动事实表：逐场活动（市场域）", [
        ("campaign_date", "DATE", "活动日期", "2026-03-15"),
        ("channel_id", "VARCHAR", "渠道编码", "CH01"),
        ("leads_count", "INTEGER", "线索数", "320"),
        ("campaign_cost", "DECIMAL(12,2)", "活动费用（元）", "18000.00"),
    ]),
    ("fact_ad_spend", "广告投放事实表：逐日投放（市场域）", [
        ("ad_date", "DATE", "投放日期", "2026-03-15"),
        ("channel_id", "VARCHAR", "渠道编码", "CH01"),
        ("impressions", "INTEGER", "曝光量", "500000"),
        ("click_count", "INTEGER", "点击量", "12000"),
        ("ad_cost", "DECIMAL(12,2)", "广告费（元）", "8600.00"),
    ]),
    # ---- 售后 After-sales ----
    ("dim_service_ticket", "服务工单维度表：工单属性（售后域）", [
        ("ticket_id", "VARCHAR", "工单号", "TS001"),
        ("ticket_type", "VARCHAR", "工单类型（安装/维修/投诉）", "维修"),
        ("priority", "VARCHAR", "优先级（高/中/低）", "高"),
    ]),
    ("fact_repair", "售后维修事实表：逐单维修（售后域）", [
        ("repair_date", "DATE", "维修日期", "2026-03-15"),
        ("customer_id", "VARCHAR", "客户编码", "C001"),
        ("repair_cost", "DECIMAL(10,2)", "维修费用（元）", "980.00"),
        ("repair_count", "INTEGER", "维修台次", "1"),
    ]),
    ("fact_complaint", "客诉事实表：逐条投诉（售后域）", [
        ("complaint_date", "DATE", "投诉日期", "2026-03-15"),
        ("customer_id", "VARCHAR", "客户编码", "C001"),
        ("complaint_type", "VARCHAR", "投诉类型（质量/交期/服务）", "质量"),
        ("complaint_count", "INTEGER", "投诉次数", "1"),
    ]),
    # ---- 研发 R&D ----
    ("dim_project", "项目维度表：研发项目（研发域）", [
        ("project_id", "VARCHAR", "项目编码", "PR001"),
        ("project_name", "VARCHAR", "项目名称", "新一代压缩机研发"),
        ("project_status", "VARCHAR", "项目状态（进行中/已结项）", "进行中"),
    ]),
    ("fact_rd_expense", "研发费用事实表：逐月投入（研发域）", [
        ("expense_month", "VARCHAR", "费用月份", "2026-03"),
        ("project_id", "VARCHAR", "项目编码", "PR001"),
        ("rd_amount", "DECIMAL(12,2)", "研发投入（元）", "150000.00"),
        ("staff_count", "INTEGER", "投入人数", "12"),
    ]),
    ("fact_patent", "专利事实表：逐件专利（研发域）", [
        ("patent_date", "DATE", "申请日期", "2026-03-15"),
        ("project_id", "VARCHAR", "项目编码", "PR001"),
        ("patent_type", "VARCHAR", "专利类型（发明/实用新型）", "发明"),
        ("patent_count", "INTEGER", "专利件数", "1"),
    ]),
    # ---- 行政 Admin ----
    ("dim_office", "办公地点维度表：办公场所（行政域）", [
        ("office_id", "VARCHAR", "场所编码", "OF01"),
        ("office_name", "VARCHAR", "场所名称", "宁波总部"),
        ("city", "VARCHAR", "所在城市", "宁波"),
    ]),
    ("fact_asset", "固定资产事实表：逐件资产（行政域）", [
        ("asset_date", "DATE", "入账日期", "2026-03-15"),
        ("asset_name", "VARCHAR", "资产名称", "叉车"),
        ("asset_value", "DECIMAL(12,2)", "资产原值（元）", "120000.00"),
        ("depreciation", "DECIMAL(10,2)", "月折旧额（元）", "950.00"),
    ]),
    # ---- IT ----
    ("dim_server", "服务器维度表：IT 资产（IT 域）", [
        ("server_id", "VARCHAR", "服务器编号", "SRV001"),
        ("server_name", "VARCHAR", "服务器名称", "erp-db-01"),
        ("server_role", "VARCHAR", "用途（数据库/应用）", "数据库"),
    ]),
    ("fact_login_log", "登录日志事实表：逐条登录（IT 域）", [
        ("login_time", "VARCHAR", "登录时间", "2026-03-15 09:00:00"),
        ("user_id", "VARCHAR", "用户账号", "u1001"),
        ("login_result", "VARCHAR", "登录结果（成功/失败）", "成功"),
    ]),
    ("fact_api_call", "接口调用事实表：逐条调用（IT 域）", [
        ("call_date", "DATE", "调用日期", "2026-03-15"),
        ("api_name", "VARCHAR", "接口名", "/api/orders"),
        ("call_count", "INTEGER", "调用次数", "15000"),
        ("avg_latency_ms", "DECIMAL(10,2)", "平均延迟（毫秒）", "120.00"),
    ]),
    # ---- 银行/融资 Finance-aux ----
    ("dim_bank", "银行维度表：合作银行（财务域）", [
        ("bank_id", "VARCHAR", "银行编码", "B001"),
        ("bank_name", "VARCHAR", "银行名称", "中国银行宁波分行"),
        ("account_no", "VARCHAR", "账号", "6222000011112222"),
    ]),
    ("fact_loan", "贷款事实表：逐笔贷款（财务域）", [
        ("loan_date", "DATE", "放款日期", "2026-03-15"),
        ("bank_id", "VARCHAR", "银行编码", "B001"),
        ("loan_amount", "DECIMAL(14,2)", "贷款金额（元）", "5000000.00"),
        ("interest_rate", "DECIMAL(6,4)", "年利率", "0.0385"),
    ]),
    ("fact_insurance", "保险事实表：逐单投保（财务域）", [
        ("ins_date", "DATE", "投保日期", "2026-03-15"),
        ("asset_name", "VARCHAR", "标的物", "厂房"),
        ("premium", "DECIMAL(12,2)", "保费（元）", "32000.00"),
    ]),
    # ---- 车辆/差旅 ----
    ("dim_vehicle", "车辆维度表：公司车辆（行政域）", [
        ("vehicle_id", "VARCHAR", "车牌号", "浙B12345"),
        ("vehicle_type", "VARCHAR", "车辆类型（轿车/货车）", "货车"),
        ("purchase_date", "DATE", "购买日期", "2021-01-15"),
    ]),
    ("fact_travel", "差旅事实表：逐次差旅（行政域）", [
        ("travel_date", "DATE", "出差日期", "2026-03-15"),
        ("employee_id", "VARCHAR", "员工工号", "E1001"),
        ("destination", "VARCHAR", "目的地", "上海"),
        ("travel_cost", "DECIMAL(10,2)", "差旅费用（元）", "1500.00"),
    ]),
    # ---- 物料 ----
    ("dim_material", "物料维度表：物料主数据（制造域）", [
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("material_name", "VARCHAR", "物料名称", "45#钢材"),
        ("material_type", "VARCHAR", "物料类型（原材料/辅料）", "原材料"),
        ("unit_price", "DECIMAL(10,2)", "采购单价（元）", "240.00"),
    ]),
    ("fact_material_usage", "物料领用事实表：逐日领用（制造域）", [
        ("usage_date", "DATE", "领用日期", "2026-03-15"),
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("usage_qty", "INTEGER", "领用数量", "120"),
        ("line", "VARCHAR", "领用产线", "产线1"),
    ]),
    # ---- 市场情报（易与销售混淆，制造干扰） ----
    ("dim_competitor", "竞争对手维度表：竞对档案（市场域）", [
        ("competitor_id", "VARCHAR", "竞对编码", "CP001"),
        ("competitor_name", "VARCHAR", "竞对名称", "A 公司"),
        ("product_line", "VARCHAR", "竞对产品线", "压缩机"),
    ]),
    ("fact_market_share", "市场份额事实表：逐月份额（市场域）", [
        ("share_month", "VARCHAR", "统计月份", "2026-03"),
        ("competitor_id", "VARCHAR", "竞对编码", "CP001"),
        ("share_rate", "DECIMAL(6,2)", "市场份额（%）", "18.50"),
    ]),
    ("fact_price_history", "历史价格事实表：逐次调价（销售辅助域）", [
        ("price_date", "DATE", "调价日期", "2026-03-15"),
        ("material_id", "VARCHAR", "物料编码", "M1001"),
        ("old_price", "DECIMAL(10,2)", "调价前价格（元）", "230.00"),
        ("new_price", "DECIMAL(10,2)", "调价后价格（元）", "240.00"),
    ]),
    # ---- 客服 ----
    ("dim_agent", "客服人员维度表：坐席档案（客服域）", [
        ("agent_id", "VARCHAR", "坐席编号", "AG001"),
        ("agent_name", "VARCHAR", "坐席姓名", "王五"),
        ("skill_group", "VARCHAR", "技能组（售前/售后）", "售后"),
    ]),
    ("fact_call_record", "呼叫记录事实表：逐通电话（客服域）", [
        ("call_date", "DATE", "通话日期", "2026-03-15"),
        ("agent_id", "VARCHAR", "坐席编号", "AG001"),
        ("call_duration", "INTEGER", "通话时长（秒）", "320"),
        ("call_result", "VARCHAR", "通话结果（已解决/转接）", "已解决"),
    ]),
]


def _build_distractors() -> List[schema.Table]:
    """把干扰表规格构造成 schema.Table 对象列表。"""
    tables: List[schema.Table] = []
    for name, comment, cols in _DISTRACTOR_SPEC:
        columns = [schema.Column(c, dt, desc, ex) for c, dt, desc, ex in cols]
        tables.append(schema.Table(name=name, comment=comment, columns=columns))
    return tables


def refresh_guard_whitelist() -> None:
    """注入干扰表后，同步刷新 sql_guard 预计算的白名单。"""
    sql_guard.TABLE_COLUMNS = {
        name: {c.name.lower() for c in table.columns} for name, table in schema.TABLES.items()
    }
    sql_guard.ALL_COLUMNS = {col for cols in sql_guard.TABLE_COLUMNS.values() for col in cols}


def schema_text_length() -> int:
    """返回当前 schema 文本字符数（近似衡量 prompt 体量）。"""
    from src.engine.prompts import build_schema_text

    return len(build_schema_text())


def run_single_case(question: str) -> Dict[str, Any]:
    """跑单条问数（只测 SQL 正确性，流程与引擎一致，不含洞察）。"""
    mock_used = text2sql.should_use_mock()
    sql = text2sql.generate_sql(question)

    guarded = sql_guard.check(sql)
    if not guarded.ok:
        return {"sql": sql, "error": f"安全校验未通过：{guarded.reason}", "mock_used": mock_used}

    try:
        result = executor.execute_sql(guarded.sql)
        return {"sql": guarded.sql, "result": result, "mock_used": mock_used}
    except ExecutionError as first_err:
        if mock_used:
            return {"sql": guarded.sql, "error": str(first_err), "mock_used": mock_used}
        fixed = text2sql.repair_sql(question, guarded.sql, str(first_err))
        if not fixed:
            return {"sql": guarded.sql, "error": str(first_err), "mock_used": mock_used}
        guarded2 = sql_guard.check(fixed)
        if not guarded2.ok:
            return {"sql": fixed, "error": f"修正后仍不安全：{guarded2.reason}", "mock_used": mock_used}
        try:
            result2 = executor.execute_sql(guarded2.sql)
            return {"sql": guarded2.sql, "result": result2, "mock_used": mock_used}
        except ExecutionError as second_err:
            return {"sql": guarded2.sql, "error": str(second_err), "mock_used": mock_used}


def run_one_table_count(n_tables: int, distractors: List[schema.Table]) -> Dict[str, Any]:
    """在「原生 6 表 + 前 (n_tables-6) 张干扰表」的 schema 下跑全部 18 条评测。"""
    base_tables = _ORIGINAL_TABLES
    extra = distractors[: max(0, n_tables - len(base_tables))]
    merged = {**base_tables, **{t.name: t for t in extra}}
    schema.TABLES = merged
    refresh_guard_whitelist()

    items: List[Dict[str, Any]] = []
    for case in EVAL_CASES:
        q = case["question"]
        t0 = time.time()
        try:
            out = run_single_case(q)
        except Exception as exc:  # noqa: BLE001 - 单条异常不中断整轮
            out = {"error": f"异常：{exc}"}
        elapsed = round(time.time() - t0, 3)

        hit = False
        if not out.get("error") and out.get("result") is not None:
            hit = evaluate_result(
                out["result"].columns, out["result"].rows, case["expected"], DEFAULT_TOLERANCE
            )

        items.append(
            {
                "id": case["id"],
                "question": q,
                "categories": case["categories"],
                "hit": hit,
                "error": out.get("error", ""),
                "mock_used": out.get("mock_used", False),
                "elapsed_seconds": elapsed,
                "sql": out.get("sql", ""),
            }
        )
        print(f"  [N={n_tables}] {case['id']} hit={hit} err={'Y' if out.get('error') else '-'} {elapsed}s")

    total = len(items)
    executed = sum(1 for it in items if not it["error"])
    hit_count = sum(1 for it in items if it["hit"])
    accuracy_of_executed = round(hit_count / executed, 4) if executed else 0.0

    return {
        "n_tables": n_tables,
        "schema_chars": schema_text_length(),
        "schema_tokens_est": round(schema_text_length() / 2),  # 中英混合粗略估算
        "metrics": {
            "total": total,
            "executed": executed,
            "sql_exec_success_rate": round(executed / total, 4),
            "hit": hit_count,
            "accuracy": round(hit_count / total, 4),
            "accuracy_of_executed": accuracy_of_executed,
            "avg_elapsed_seconds": round(
                sum(it["elapsed_seconds"] for it in items) / total, 3
            ),
        },
        "items": items,
    }


def main() -> None:
    print("=" * 70)
    print("表数量扩展压力测试（Text-to-SQL 表推理上限实测）")
    print(f"模型：{config.MODEL_NAME}｜模式：{'mock' if text2sql.should_use_mock() else 'LLM（真实大模型）'}")
    print(f"评测集：{len(EVAL_CASES)} 条｜目标表数量：{TABLE_COUNTS}")
    print("=" * 70)

    distractors = _build_distractors()
    print(f"干扰表池：{len(distractors)} 张（可覆盖到 {len(_ORIGINAL_TABLES) + len(distractors)} 张）\n")

    results: List[Dict[str, Any]] = []
    for n in TABLE_COUNTS:
        print(f"\n>>> 表数量 N={n} 开始 ...")
        t0 = time.time()
        r = run_one_table_count(n, distractors)
        r["wall_seconds"] = round(time.time() - t0, 1)
        results.append(r)
        print(f">>> N={n} 完成：执行成功率 {r['metrics']['sql_exec_success_rate']*100:.1f}%｜"
              f"准确率 {r['metrics']['accuracy']*100:.1f}%｜耗时 {r['wall_seconds']}s")

    # 还原原始 schema
    schema.TABLES = deepcopy(_ORIGINAL_TABLES)
    refresh_guard_whitelist()

    # 落盘 JSON
    report = {
        "meta": {
            "project": "宁波鲍斯能源装备智能问数平台",
            "experiment": "表数量 → Text-to-SQL 正确性",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "model": config.MODEL_NAME,
            "mode": "mock" if text2sql.should_use_mock() else "llm",
            "eval_cases": len(EVAL_CASES),
            "tolerance": DEFAULT_TOLERANCE,
            "table_counts": TABLE_COUNTS,
        },
        "results": results,
    }
    json_path = REPORT_DIR / "table_scaling_experiment.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 落盘 Markdown 汇总
    md_path = REPORT_DIR / "table_scaling_experiment.md"
    lines = [
        "# 表数量扩展压力测试（Text-to-SQL 表推理上限实测）",
        "",
        f"- 模型：`{config.MODEL_NAME}`",
        f"- 模式：`{'mock' if text2sql.should_use_mock() else 'LLM（真实大模型）'}`",
        f"- 评测集：`{len(EVAL_CASES)}` 条（温度 0，失败自动修正一次）",
        f"- 容差：相对 1% + 绝对 0.01",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "| 表数量 | schema 字符 | 估算 token | SQL 执行成功率 | 结果准确率 | 相对成功执行 | 平均延迟(s) | 整轮耗时(s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        m = r["metrics"]
        lines.append(
            f"| {r['n_tables']} | {r['schema_chars']} | {r['schema_tokens_est']} | "
            f"{m['sql_exec_success_rate']*100:.1f}% ({m['executed']}/{m['total']}) | "
            f"{m['accuracy']*100:.1f}% ({m['hit']}/{m['total']}) | "
            f"{m['accuracy_of_executed']*100:.1f}% | {m['avg_elapsed_seconds']} | {r['wall_seconds']} |"
        )
    lines += [
        "",
        "## 各表数量下失败的用例",
        "",
    ]
    for r in results:
        failed = [it for it in r["items"] if not it["hit"]]
        if failed:
            lines.append(f"### N={r['n_tables']}（{len(failed)} 条未命中）")
            for it in failed:
                lines.append(f"- {it['id']} {it['question']}｜err={it['error'] or '结果不符'}")
            lines.append("")
        else:
            lines.append(f"### N={r['n_tables']}：全部 18 条命中 ✅")
            lines.append("")

    lines += [
        "## 结论要点（待人工补充解读）",
        "",
        "- 观察「表数量上升时，准确率是否下降、先坏的是哪类用例（多表关联/条件过滤/时间范围）」；",
        "- 观察「SQL 执行成功率」与「结果准确率」的分化点；",
        "- 据此给出「全量 schema 注入」的舒适区边界，以及何时应切换 RAG 动态挑表。",
        "",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n" + "=" * 70)
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{md_path}")
    print("=" * 70)


# 保存原生 6 张表（运行结束后还原）
_ORIGINAL_TABLES: Dict[str, schema.Table] = deepcopy(schema.TABLES)

if __name__ == "__main__":
    main()
