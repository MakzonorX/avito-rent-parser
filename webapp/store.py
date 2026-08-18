"""Хранилище найденных объявлений для веб-интерфейса (SQLite)."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from models import Item
from rent.extract import extract

DB_PATH = Path("storage/webapp.db")
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ads (
                id            INTEGER PRIMARY KEY,
                title         TEXT,
                price         INTEGER,
                rooms         INTEGER,
                area          REAL,
                floor         INTEGER,
                total_floors  INTEGER,
                address       TEXT,
                description   TEXT,
                url           TEXT,
                images        TEXT,
                published_at  TEXT,
                found_at      TEXT,
                notified      INTEGER DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ads_found_at ON ads(found_at DESC)")


def save_ad(ad: Item, notified: bool = True) -> None:
    info = extract(ad, max_photos=10)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """
                INSERT INTO ads (id, title, price, rooms, area, floor, total_floors,
                                 address, description, url, images, published_at, found_at, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    price = excluded.price,
                    found_at = excluded.found_at,
                    notified = excluded.notified
                """,
                (
                    ad.id, info.title, info.price, info.rooms, info.area, info.floor,
                    info.total_floors, info.address, info.description, info.url,
                    json.dumps(info.images, ensure_ascii=False),
                    info.published_at.isoformat() if info.published_at else None,
                    now, int(notified),
                ),
            )
    except sqlite3.Error as err:
        logger.warning(f"Не удалось сохранить объявление {ad.id}: {err}")


def list_ads(limit: int = 50, offset: int = 0, search: str = "") -> list[dict]:
    query = "SELECT * FROM ads"
    params: list = []
    if search:
        query += " WHERE title LIKE ? OR address LIKE ? OR description LIKE ?"
        like = f"%{search}%"
        params += [like, like, like]
    query += " ORDER BY found_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            item["images"] = json.loads(item["images"] or "[]")
        except json.JSONDecodeError:
            item["images"] = []
        result.append(item)
    return result


def stats() -> dict:
    with _lock, _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM ads WHERE found_at >= ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00"),),
        ).fetchone()[0]
        last = conn.execute("SELECT found_at FROM ads ORDER BY found_at DESC LIMIT 1").fetchone()
        avg_price = conn.execute("SELECT AVG(price) FROM ads WHERE price > 0").fetchone()[0]
    return {
        "total": total,
        "today": today,
        "last_found_at": last[0] if last else None,
        "avg_price": int(avg_price) if avg_price else None,
    }


def clear() -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM ads")
    logger.info("История найденных объявлений очищена")
