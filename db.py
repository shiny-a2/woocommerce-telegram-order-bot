"""لایه‌ی کوچک SQLite: نگاشت سفارش→پیام، وضعیت، کپشن، موقعیت، و متادیتا (خط مبنا)."""
from __future__ import annotations

import os
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def init():
    global _conn
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            order_id       INTEGER PRIMARY KEY,
            message_id     INTEGER,
            chat_id        INTEGER,
            status         TEXT,
            stock_location TEXT,
            caption        TEXT,
            posted_at      REAL
        )"""
    )
    for col in ("status TEXT", "stock_location TEXT", "caption TEXT"):
        try:
            _conn.execute(f"ALTER TABLE orders ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS seen_notes (
            note_id  INTEGER PRIMARY KEY,
            order_id INTEGER
        )"""
    )
    _conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    _conn.execute("CREATE TABLE IF NOT EXISTS sent_leads (order_id INTEGER PRIMARY KEY)")
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS lead_outcomes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id  INTEGER,
            action    TEXT,
            user_id   INTEGER,
            user_name TEXT,
            ts        REAL
        )"""
    )
    _conn.commit()


def lead_sent(order_id) -> bool:
    with _lock:
        return _conn.execute("SELECT 1 FROM sent_leads WHERE order_id=?", (order_id,)).fetchone() is not None


def mark_lead(order_id):
    with _lock:
        _conn.execute("INSERT OR IGNORE INTO sent_leads(order_id) VALUES (?)", (order_id,))
        _conn.commit()


def record_lead_outcome(order_id, action, user_id, user_name):
    with _lock:
        _conn.execute(
            "INSERT INTO lead_outcomes(order_id, action, user_id, user_name, ts) VALUES (?,?,?,?,?)",
            (order_id, action, user_id, user_name, time.time()),
        )
        _conn.commit()


def outcomes_since(since_ts):
    with _lock:
        cur = _conn.execute(
            "SELECT order_id, action, user_id, user_name, ts FROM lead_outcomes WHERE ts>=? ORDER BY ts DESC",
            (since_ts,),
        )
        return cur.fetchall()


def get_meta(key):
    with _lock:
        row = _conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


def set_meta(key, value):
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, str(value)))
        _conn.commit()


def count_orders() -> int:
    with _lock:
        return _conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def is_posted(order_id: int) -> bool:
    with _lock:
        return _conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone() is not None


def mark_posted(order_id, message_id, chat_id, status=None, stock_location=None, caption=None):
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO orders(order_id, message_id, chat_id, status, stock_location, caption, posted_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (order_id, message_id, chat_id, status, stock_location, caption, time.time()),
        )
        _conn.commit()


def update_after_edit(order_id, status, caption):
    with _lock:
        _conn.execute(
            "UPDATE orders SET status=?, caption=? WHERE order_id=?", (status, caption, order_id)
        )
        _conn.commit()


def get_message(order_id: int):
    with _lock:
        row = _conn.execute(
            "SELECT message_id, chat_id FROM orders WHERE order_id=?", (order_id,)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)


def get_edit_row(order_id):
    """(message_id, chat_id, status, caption, stock_location) یا تاپلِ None."""
    with _lock:
        row = _conn.execute(
            "SELECT message_id, chat_id, status, caption, stock_location FROM orders WHERE order_id=?",
            (order_id,),
        ).fetchone()
        return row if row else (None, None, None, None, None)


def tracked_orders(since_ts: float):
    """سفارش‌هایی که واقعاً پست شده‌اند (message_id<>0) و در بازه‌ی اخیرند — برای ویرایش کپشن."""
    with _lock:
        cur = _conn.execute(
            "SELECT order_id FROM orders WHERE posted_at>=? AND message_id IS NOT NULL AND message_id<>0 "
            "ORDER BY order_id DESC",
            (since_ts,),
        )
        return [r[0] for r in cur.fetchall()]
