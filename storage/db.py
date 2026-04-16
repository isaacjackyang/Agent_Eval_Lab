from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS run_summaries (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    fitness REAL NOT NULL,
    elapsed_sec REAL NOT NULL,
    retries INTEGER NOT NULL,
    failure_tags TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def insert_run_summary(db_path: Path, summary: dict) -> None:
    payload = (
        summary["run_id"],
        summary["task_id"],
        summary["config_id"],
        summary["status"],
        summary["score"],
        summary["fitness"],
        summary["elapsed_sec"],
        summary["retries"],
        json.dumps(summary["failure_tags"], ensure_ascii=False),
        summary["created_at"],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_summaries (
                run_id,
                task_id,
                config_id,
                status,
                score,
                fitness,
                elapsed_sec,
                retries,
                failure_tags,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
