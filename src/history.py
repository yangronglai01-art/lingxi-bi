# -*- coding: utf-8 -*-
"""历史记录与收藏：SQLite 持久化每次问数。

本模块被 FastAPI（src/api/main.py）与 Streamlit（src/app.py）共用，
二者写入同一个 data/history.db，保证「接口侧」「工作台侧」记录互通。

存储策略：不保存完整结果集（可能上万行），只保存结果摘要——
「行数 × 列数」+ 前 5 行样例，兼顾可回溯性与磁盘占用。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from src import config

# 摘要里保存的最大样例行数
SAMPLE_ROWS = 5


class HistoryStore:
    """SQLite 历史记录仓库。"""

    def __init__(self, db_path: Optional[Path | str] = None):
        """初始化并建表（幂等）。

        参数:
            db_path: 数据库文件路径，默认 data/history.db。
        """
        self.db_path = Path(db_path) if db_path else config.DATA_DIR / "history.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ 内部
    def _connect(self) -> sqlite3.Connection:
        """建立连接（Row 工厂，便于按列名取值）。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """建表（幂等）。"""
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    question    TEXT    NOT NULL,
                    sql         TEXT,
                    summary     TEXT,
                    columns     TEXT,          -- JSON: 列名列表
                    sample_rows TEXT,          -- JSON: 前几行样例
                    chart_type  TEXT,
                    insights    TEXT,          -- JSON: 洞察列表
                    error       TEXT,
                    mock_used   INTEGER NOT NULL DEFAULT 0,
                    favorited   INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT    NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _summary_text(result: Any) -> str:
        """根据查询结果生成摘要文本（行数 × 列数）。"""
        if result is None:
            return "无结果"
        return f"共 {result.row_count} 行 × {len(result.columns)} 列"

    # ------------------------------------------------------------------ 写入
    def add(
        self,
        question: str,
        sql: str = "",
        result: Any = None,
        chart: Any = None,
        insights: Optional[List[str]] = None,
        error: str = "",
        mock_used: bool = False,
    ) -> int:
        """保存一条问数记录，返回新记录 ID。"""
        columns = json.dumps(result.columns, ensure_ascii=False) if result is not None else None
        sample = (
            json.dumps(result.rows[:SAMPLE_ROWS], ensure_ascii=False)
            if result is not None
            else None
        )
        insight_json = json.dumps(insights or [], ensure_ascii=False)
        chart_type = chart.chart_type if chart is not None else ""

        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO history
                    (question, sql, summary, columns, sample_rows, chart_type,
                     insights, error, mock_used, favorited, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    question,
                    sql,
                    self._summary_text(result),
                    columns,
                    sample,
                    chart_type,
                    insight_json,
                    error,
                    int(mock_used),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    # ------------------------------------------------------------------ 查询
    def _row_to_item(self, row: sqlite3.Row) -> dict:
        """把数据库行还原为可 JSON 化的字典。"""
        item = dict(row)
        item["columns"] = json.loads(item["columns"]) if item["columns"] else []
        item["sample_rows"] = json.loads(item["sample_rows"]) if item["sample_rows"] else []
        item["insights"] = json.loads(item["insights"]) if item["insights"] else []
        item["favorited"] = bool(item["favorited"])
        item["mock_used"] = bool(item["mock_used"])
        return item

    def list(self, favorite_only: bool = False, limit: int = 100) -> List[dict]:
        """返回历史列表（倒序，最新在前）。"""
        conn = self._connect()
        try:
            sql = "SELECT * FROM history"
            if favorite_only:
                sql += " WHERE favorited = 1"
            sql += " ORDER BY id DESC LIMIT ?"
            rows = conn.execute(sql, (limit,)).fetchall()
            return [self._row_to_item(r) for r in rows]
        finally:
            conn.close()

    def get(self, history_id: int) -> Optional[dict]:
        """返回单条历史，不存在返回 None。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM history WHERE id = ?", (history_id,)).fetchone()
            return self._row_to_item(row) if row else None
        finally:
            conn.close()

    def toggle_favorite(self, history_id: int) -> Optional[int]:
        """收藏 / 取消收藏，返回新的 favorited 值（不存在返回 None）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT favorited FROM history WHERE id = ?", (history_id,)
            ).fetchone()
            if row is None:
                return None
            new_val = 1 - int(row["favorited"])
            conn.execute("UPDATE history SET favorited = ? WHERE id = ?", (new_val, history_id))
            conn.commit()
            return new_val
        finally:
            conn.close()

    def delete(self, history_id: int) -> bool:
        """删除一条历史，返回是否成功。"""
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM history WHERE id = ?", (history_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
