@echo off
chcp 65001 >nul
cd /d %~dp0..
streamlit run src/app.py --server.port 8501
