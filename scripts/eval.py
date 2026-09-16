# -*- coding: utf-8 -*-
"""评测脚本：批量跑 Text-to-SQL，统计执行成功率与结果准确率，输出 JSON 报告。

用法（在项目根目录执行）：
    # 真实评测（优先走 LLM，需 .env 配好 API_KEY）
    python scripts/eval.py

    # 离线评测（mock 规则生成 SQL，无需网络）
    python scripts/eval.py --mock

    # 指定输出文件
    python scripts/eval.py --output reports/eval_report.json

统计口径：
- SQL 执行成功率：引擎能成功生成并执行 SQL、返回结果（无 error）的用例占比。
- 结果准确率：执行结果与参考答案（关键指标值）在容差内一致的用例占比，
  对分组 / TopN / 时间序列结果做「集合比对 + 数值容差」。

输出：reports/eval_report.json（含 meta / metrics / items 三层）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 统一 UTF-8 输出，修复 Windows GBK 控制台中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 部分环境无 reconfigure
    pass

# 允许以脚本方式直接运行时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.engine.engine import QueryEngine  # noqa: E402
from src.eval_cases import DEFAULT_TOLERANCE, EVAL_CASES, evaluate_result  # noqa: E402

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


def run_evaluation(use_mock: bool) -> Dict[str, Any]:
    """批量执行评测集，返回完整报告结构。

    参数:
        use_mock: True 时强制走 mock 规则生成 SQL（离线）。

    返回:
        报告 dict（meta + metrics + items）。
    """
    # 强制 mock 模式（在实例化引擎前覆盖配置，保证 offline 可复现）
    if use_mock:
        config.ENABLE_MOCK = True

    engine = QueryEngine()
    items: List[Dict[str, Any]] = []

    for case in EVAL_CASES:
        question = case["question"]
        t0 = time.time()

        ans = engine.ask(question)
        elapsed = round(time.time() - t0, 3)

        hit = False
        if ans.error:
            hit = False
        elif ans.result is not None:
            hit = evaluate_result(
                ans.result.columns,
                ans.result.rows,
                case["expected"],
                DEFAULT_TOLERANCE,
            )

        # 结果摘要：最多保留前 10 行，避免报告体积过大
        result_summary = None
        if ans.result is not None:
            result_summary = {
                "columns": ans.result.columns,
                "rows": ans.result.rows[:10],
                "row_count": ans.result.row_count,
            }

        items.append(
            {
                "id": case["id"],
                "question": question,
                "categories": case["categories"],
                "expected": case["expected"],
                "sql": ans.sql,
                "mock_used": ans.mock_used,
                "error": ans.error,
                "hit": hit,
                "result": result_summary,
                "elapsed_seconds": elapsed,
            }
        )

    total = len(items)
    executed = sum(1 for it in items if not it["error"])
    hit_count = sum(1 for it in items if it["hit"])
    # 准确率两种口径：相对全部用例、相对成功执行的用例
    exec_rate = round(executed / total, 4) if total else 0.0
    accuracy = round(hit_count / total, 4) if total else 0.0
    accuracy_of_executed = round(hit_count / executed, 4) if executed else 0.0

    metrics = {
        "total": total,
        "executed": executed,
        "sql_exec_success_rate": exec_rate,
        "hit": hit_count,
        "accuracy": accuracy,
        "accuracy_of_executed": accuracy_of_executed,
    }

    meta = {
        "project": "恒岳汽车零部件经营分析智能问数系统",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "mock" if use_mock else "llm",
        "model": config.MODEL_NAME if not use_mock else "mock-rules",
        "tolerance": DEFAULT_TOLERANCE,
    }

    return {"meta": meta, "metrics": metrics, "items": items}


def main() -> None:
    """解析命令行参数并执行评测，落盘 JSON 报告。"""
    parser = argparse.ArgumentParser(description="恒岳 BI Text-to-SQL 评测脚本")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="离线评测：用规则生成 SQL，不调用大模型",
    )
    parser.add_argument(
        "--output",
        default=str(REPORT_DIR / "eval_report.json"),
        help="报告输出路径（默认 reports/eval_report.json）",
    )
    args = parser.parse_args()

    print(f"评测模式：{'mock（离线）' if args.mock else 'LLM（真实大模型）'}")
    print(f"评测用例：{len(EVAL_CASES)} 条\n")

    report = run_evaluation(use_mock=args.mock)

    # 写报告
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    m = report["metrics"]
    print("=" * 56)
    print(f"SQL 执行成功率：{m['sql_exec_success_rate'] * 100:.1f}%  "
          f"({m['executed']}/{m['total']})")
    print(f"结果准确率    ：{m['accuracy'] * 100:.1f}%  ({m['hit']}/{m['total']})")
    print(f"  （相对成功执行：{m['accuracy_of_executed'] * 100:.1f}%）")
    print(f"报告已写入：{out_path}")
    print("=" * 56)


if __name__ == "__main__":
    main()
