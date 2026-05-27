"""
db.py — SQLite-backed deduplication store.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

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
        conn.commit()
    logger.info("Database ready at %s", _db_path())


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
