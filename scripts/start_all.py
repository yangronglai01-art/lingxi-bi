# -*- coding: utf-8 -*-
"""一键启动：同时拉起 FastAPI 接口 + Streamlit 工作台。

用法：
    python scripts/start_all.py

- FastAPI   → http://localhost:8000/docs（接口文档）
- Streamlit → http://localhost:8501（工作台）
"""

from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Windows 控制台默认可能使用 GBK 编码，这里统一为 UTF-8，保证中文输出不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 部分环境无 reconfigure
    pass

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    """并行启动 API 与前端，并自动打开浏览器。"""
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT),
    )
    ui = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "src/app.py", "--server.port", "8501"],
        cwd=str(ROOT),
    )

    print("✅ 恒岳 BI 已启动：")
    print("   · API 接口：http://localhost:8000/docs")
    print("   · 工作台  ：http://localhost:8501")
    print("按 Ctrl+C 退出……")

    time.sleep(2)
    try:
        webbrowser.open("http://localhost:8501")
    except Exception:  # noqa: BLE001 - 无浏览器环境忽略
        pass

    try:
        api.wait()
        ui.wait()
    except KeyboardInterrupt:
        print("\n正在停止……")
        api.terminate()
        ui.terminate()


if __name__ == "__main__":
    main()
