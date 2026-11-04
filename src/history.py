# -*- coding: utf-8 -*-
"""历史记录与收藏：PostgreSQL 持久化每次问数。

本模块被 FastAPI（src/api/main.py / src/petkit/api.py）与 React 前端共用，
二者写入同一个 Postgres 实例，保证「接口侧」「前端侧」记录互通。

存储策略：不保存完整结果集（可能上万行），只保存结果摘要——
「行数 × 列数」+ 前 5 行样例，兼顾可回溯性与磁盘占用。

隔离策略：用 ``scope`` 列取代旧版的「每个主体一个 SQLite 文件」；petkit 侧
把授权主体的摘要哈希作为 scope 传入，实现与旧文件级隔离一致的语义。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, List, Optional

from src import storage

# 摘要里保存的最大样例行数
SAMPLE_ROWS = 5


class HistoryStore:
    """PostgreSQL 历史记录仓库（按 scope 隔离）。"""

    def __init__(self, db_path=None, *, scope: Optional[str] = None):
        """初始化并建表（幂等）。

        参数:
            db_path: 兼容旧签名；若未显式给出 scope，则用它派生稳定隔离键。
            scope:   隔离命名空间。默认 legacy。
        """
        if scope is None:
            scope = (
                "legacy"
                if db_path is None
                else hashlib.sha256(str(db_path).encode()).hexdigest()
            )
        self.scope = scope
        # 兼容旧属性：不同 scope 对应不同「逻辑库」，供旧调用方比较隔离性。
        self.db_path = scope
        self._init_db()

    # ------------------------------------------------------------------ 内部
    def _init_db(self) -> None:
        """建表（幂等）。"""
        with storage.db() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id          BIGSERIAL PRIMARY KEY,
                    scope       TEXT    NOT NULL,
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
                );
                CREATE INDEX IF NOT EXISTS history_scope_id ON history(scope, id);
                """
            )

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

        with storage.db() as c:
            row = c.execute(
                """
                INSERT INTO history
                    (scope, question, sql, summary, columns, sample_rows, chart_type,
                     insights, error, mock_used, favorited, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                RETURNING id
                """,
                (
                    self.scope,
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
            ).fetchone()
            return row[0]

    # ------------------------------------------------------------------ 查询
    def _row_to_item(self, row) -> dict:
        """把数据库行还原为可 JSON 化的字典。"""
        item = dict(row)
        item.pop("scope", None)
        item["columns"] = json.loads(item["columns"]) if item["columns"] else []
        item["sample_rows"] = json.loads(item["sample_rows"]) if item["sample_rows"] else []
        item["insights"] = json.loads(item["insights"]) if item["insights"] else []
        item["favorited"] = bool(item["favorited"])
        item["mock_used"] = bool(item["mock_used"])
        return item

    def list(self, favorite_only: bool = False, limit: int = 100) -> List[dict]:
        """返回历史列表（倒序，最新在前）。"""
        with storage.db() as c:
            sql = "SELECT * FROM history WHERE scope = ?"
            if favorite_only:
                sql += " AND favorited = 1"
            sql += " ORDER BY id DESC LIMIT ?"
            rows = c.execute(sql, (self.scope, limit)).fetchall()
            return [self._row_to_item(r) for r in rows]

    def get(self, history_id: int) -> Optional[dict]:
        """返回单条历史，不存在返回 None。"""
        with storage.db() as c:
            row = c.execute(
                "SELECT * FROM history WHERE id = ? AND scope = ?",
                (history_id, self.scope),
            ).fetchone()
            return self._row_to_item(row) if row else None

    def toggle_favorite(self, history_id: int) -> Optional[int]:
        """收藏 / 取消收藏，返回新的 favorited 值（不存在返回 None）。"""
        with storage.db() as c:
            row = c.execute(
                "SELECT favorited FROM history WHERE id = ? AND scope = ?",
                (history_id, self.scope),
            ).fetchone()
            if row is None:
                return None
            new_val = 1 - int(row["favorited"])
            c.execute(
                "UPDATE history SET favorited = ? WHERE id = ? AND scope = ?",
                (new_val, history_id, self.scope),
            )
            return new_val

    def delete(self, history_id: int) -> bool:
        """删除一条历史，返回是否成功。"""
        with storage.db() as c:
            c.execute(
                "DELETE FROM history WHERE id = ? AND scope = ?",
                (history_id, self.scope),
            )
            return c.rowcount > 0
