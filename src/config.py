# -*- coding: utf-8 -*-
"""全局配置：路径、随机种子与数据时间范围。

所有与「可复现性」相关的常量集中于此，保证同一份脚本在任何机器、
任何时间运行都生成完全一致的数据。
"""

import os
from pathlib import Path

try:  # 读取 .env 环境变量（若 python-dotenv 已安装）
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - 无 python-dotenv 时退化为仅读系统环境变量
    pass

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
# 项目根目录（本文件位于 src/ 下，向上两级即为项目根）
ROOT_DIR = Path(__file__).resolve().parent.parent

# 数据根目录：存放生成的 CSV 与 DuckDB 数仓文件
DATA_DIR = ROOT_DIR / "data"

# 原始数据 CSV 输出目录（用于人工核对 / 数据血缘，灌数以内存 DataFrame 为准）
CSV_DIR = DATA_DIR / "csv"

# DuckDB 数仓文件路径
WAREHOUSE_PATH = DATA_DIR / "lingxi.duckdb"

# ---------------------------------------------------------------------------
# 可复现性
# ---------------------------------------------------------------------------
# 全局随机种子（固定）。所有数据生成逻辑均基于此种子派生的 random.Random 实例。
RANDOM_SEED = 20240916

# ---------------------------------------------------------------------------
# 数据时间范围：最近 12 个月（固定区间，保证可复现）
# ---------------------------------------------------------------------------
START_DATE = "2025-09-01"  # 起始日期（含）
END_DATE = "2026-08-31"  # 结束日期（含）

# ---------------------------------------------------------------------------
# 大模型（LLM）配置：OpenAI 兼容接口（Text-to-SQL / AI 洞察用）
# ---------------------------------------------------------------------------
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "deepseek")  # 服务商（仅记录）
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-v4-pro")  # 模型名
API_KEY = os.getenv("API_KEY", "")  # API Key
BASE_URL = os.getenv("BASE_URL", "https://api.deepseek.com/v1")  # 兼容接口根地址
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))  # 请求超时（秒）
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "1"))  # SQL 自动修正重试次数

# mock 降级开关：为 true 时，即使无 API/无网络也用「规则生成 SQL」跑通全流程
ENABLE_MOCK = os.getenv("ENABLE_MOCK", "false").strip().lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# 查询引擎配置
# ---------------------------------------------------------------------------
MAX_RESULT_ROWS = 1000  # SQL 安全校验强制的结果行数上限（LIMIT）
