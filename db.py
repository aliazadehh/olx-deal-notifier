"""
db.py — SQLite-backed deduplication store.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent / os.getenv("DB_PATH", "seen_listings.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS seen_listings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   TEXT UNIQUE NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT,
    price_pln    REAL,
    product_key  TEXT,
    condition    TEXT,
    notified_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_RUN_LOG_SQL = """
CREATE TABLE IF NOT EXISTS run_log (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT
);
"""


def _db_path() -> Path:
    env = os.getenv("DB_PATH")
    return Path(env) if env else Path(__file__).parent / "seen_listings.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    logger.debug("Initializing database at %s", _db_path())
    with get_connection() as conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_RUN_LOG_SQL)
        conn.commit()
    logger.info("Database ready at %s", _db_path())


def get_last_run() -> Optional[datetime]:
    with get_connection() as conn:
        row = conn.execute("SELECT last_run_at FROM run_log WHERE id = 1").fetchone()
    if row and row["last_run_at"]:
        dt = datetime.fromisoformat(str(row["last_run_at"]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def set_last_run(dt: datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_log (id, last_run_at) VALUES (1, ?)",
            (dt.isoformat(),),
        )
        conn.commit()


def is_seen(listing_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
    return row is not None


def mark_seen(
    listing_id: str,
    url: str,
    title: str,
    price_pln: float,
    product_key: str,
    condition: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO seen_listings
                (listing_id, url, title, price_pln, product_key, condition, notified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (listing_id, url, title, price_pln, product_key, condition, datetime.utcnow()),
        )
        conn.commit()
    logger.debug("Marked listing %s as seen", listing_id)
