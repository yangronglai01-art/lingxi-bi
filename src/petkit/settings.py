"""Independent artifacts: the original six-table warehouse is never overwritten."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.getenv("PETKIT_DATA_DIR", str(ROOT / "data" / "petkit500"))).resolve()
CURRENT = DATA / "current.json"
SEED = 20261016
START = "2025-10-01"
END = "2026-09-30"
AS_OF = "2026-10-16"
VERSION = "petkit-v1.3"
MAX_ROWS = 1000
MAX_BYTES = 2_000_000
QUERY_SECONDS = max(1, int(os.getenv("PETKIT_QUERY_SECONDS", "15")))
MODE = os.getenv("PETKIT_MODE", "demo")


def artifacts():
    import json
    if not CURRENT.exists():
        raise FileNotFoundError("请先运行 python scripts/build_petkit500.py")
    manifest = json.loads(CURRENT.read_text(encoding="utf-8"))
    version_dir = DATA / manifest["generation"]
    if version_dir.parent.resolve() != DATA.resolve():
        raise ValueError("无效数据版本路径")
    return version_dir, manifest
