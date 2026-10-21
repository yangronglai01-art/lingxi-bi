"""Workbench profile dispatcher."""
import runpy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src import config
if config.PROJECT_PROFILE == "legacy":
    runpy.run_module("src.app_legacy",run_name="__main__")
else:
    from src.petkit.workbench import main
    main()
