# -*- coding: utf-8 -*-
"""端到端问数入口。

用法：
    python scripts/ask.py "华东地区销售额是多少"
    python scripts/ask.py "各产品的缺陷率是多少"
    python scripts/ask.py "2026年上半年每个月的销售额趋势"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认可能使用 GBK 编码，这里统一为 UTF-8，保证中文输出不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 部分环境无 reconfigure
    pass

# 允许以脚本方式直接运行时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.engine import QueryEngine  # noqa: E402


def _print_answer(ans) -> None:
    """把 Answer 可读地打印到控制台。"""
    print("=" * 72)
    print("问题：", ans.question)
    if ans.error:
        print("错误：", ans.error)
        print("=" * 72)
        return
    print("SQL：", ans.sql)
    print("Mock 降级：", "是" if ans.mock_used else "否")
    if ans.result:
        print("结果列：", ans.result.columns)
        print(f"返回 {ans.result.row_count} 行：")
        for row in ans.result.rows[:20]:
            print("   ", row)
        if ans.result.row_count > 20:
            print(f"   ... 其余 {ans.result.row_count - 20} 行省略")
    if ans.chart:
        print("图表类型：", ans.chart.chart_type, "｜", ans.chart.reason)
    if ans.insights:
        print("洞察：")
        for ins in ans.insights:
            print("  -", ins)
    print("=" * 72)


def main() -> None:
    if len(sys.argv) < 2:
        print('用法：python scripts/ask.py "你的问题"')
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    engine = QueryEngine()
    ans = engine.ask(question)
    _print_answer(ans)


if __name__ == "__main__":
    main()
