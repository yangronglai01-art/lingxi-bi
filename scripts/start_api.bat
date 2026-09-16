@echo off
chcp 65001 >nul
cd /d %~dp0..
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
