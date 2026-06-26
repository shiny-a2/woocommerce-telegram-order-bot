"""لایه‌ی کوچک SQLite: نگاشت سفارش→پیام، وضعیت، کپشن و موقعیت موجودی.

کپشن آخرین‌بار ارسال‌شده ذخیره می‌شود تا هنگام تغییر، فقط در صورت اختلاف ویرایش شود.
"""
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
    # مهاجرت برای دیتابیس‌های قدیمی
    for col in ("status TEXT", "stock_location TEXT", "caption TEXT"):
        try:
            _conn.execute(f"ALTER TABLE orders ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
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
