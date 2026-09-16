# -*- coding: utf-8 -*-
"""一键构建脚本：生成数据 → 建库灌数 → 校验摘要。

用法（在项目根目录执行）:
    python scripts/build_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认可能使用 GBK 编码，这里统一为 UTF-8，保证中文输出不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 将项目根目录加入模块搜索路径，保证可 `from src import ...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.warehouse import build_warehouse, validate_and_summarize  # noqa: E402


def main() -> None:
    """执行完整的数据层构建流程。"""
    print(f"随机种子  : {config.RANDOM_SEED}")
    print(f"时间范围  : {config.START_DATE} ~ {config.END_DATE}")

    print("\n步骤 1/3：生成数据 ...")
    dfs = generate_all()

    print("步骤 2/3：建库灌数 ...")
    conn = build_warehouse(dfs)
    print(f"数仓文件  : {config.WAREHOUSE_PATH}")

    print("步骤 3/3：校验与摘要 ...")
    validate_and_summarize(conn)
    conn.close()

    print("\n✅ 数据层构建完成。")


if __name__ == "__main__":
    main()
