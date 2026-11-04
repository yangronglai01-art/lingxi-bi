# -*- coding: utf-8 -*-
"""一键启动：拉起 FastAPI 接口，并同源托管 React 工作台（web/dist）。

用法：
    python scripts/start_all.py

- React 工作台 → http://localhost:8000
- FastAPI 文档 → http://localhost:8000/docs
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
    """启动 API（同源托管 React 工作台），并自动打开浏览器。"""
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
    )

    print("✅ 灵犀 · 智能问数平台已启动：")
    print("   · 工作台（React）：http://localhost:8000")
    print("   · API 接口      ：http://localhost:8000/docs")
    print("按 Ctrl+C 退出……")

    time.sleep(2)
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:  # noqa: BLE001 - 无浏览器环境忽略
        pass

    try:
        api.wait()
    except KeyboardInterrupt:
        print("\n正在停止……")
        api.terminate()


if __name__ == "__main__":
    main()
