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
    for col in ("status TEXT", "stock_location TEXT", "caption TEXT", "date_modified TEXT"):
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
    _conn.execute("CREATE TABLE IF NOT EXISTS due_sent (k TEXT PRIMARY KEY)")
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS recovery (
            order_id   INTEGER PRIMARY KEY,
            phone      TEXT,
            created_ts REAL,
            sent1_at   REAL,
            sent2_at   REAL,
            paid       INTEGER DEFAULT 0,
            recovered  REAL DEFAULT 0,
            updated_at REAL
        )"""
    )
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
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS crm_actions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            phone     TEXT,
            action    TEXT,
            detail    TEXT,
            user_id   INTEGER,
            user_name TEXT,
            ts        REAL
        )"""
    )
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS wc_sync_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint     TEXT,
            pages        INTEGER,
            items        INTEGER,
            requests     INTEGER,
            duration_ms  INTEGER,
            error        TEXT,
            ts           REAL
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


def due_sent(key) -> bool:
    with _lock:
        return _conn.execute("SELECT 1 FROM due_sent WHERE k=?", (key,)).fetchone() is not None


def mark_due_sent(key):
    with _lock:
        _conn.execute("INSERT OR IGNORE INTO due_sent(k) VALUES (?)", (key,))
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


def record_crm_action(phone, action, user_id, user_name, detail=""):
    with _lock:
        _conn.execute(
            "INSERT INTO crm_actions(phone, action, detail, user_id, user_name, ts) VALUES (?,?,?,?,?,?)",
            (str(phone), action, detail or "", user_id, user_name, time.time()),
        )
        _conn.commit()


def crm_actions_since(since_ts):
    with _lock:
        cur = _conn.execute(
            "SELECT phone, action, detail, user_id, user_name, ts FROM crm_actions WHERE ts>=? ORDER BY ts DESC",
            (since_ts,),
        )
        return cur.fetchall()


# ---------- بازیابیِ پرداختِ ناموفق ----------
def recovery_row(order_id):
    with _lock:
        r = _conn.execute(
            "SELECT order_id, phone, created_ts, sent1_at, sent2_at, paid, recovered FROM recovery WHERE order_id=?",
            (order_id,),
        ).fetchone()
    if not r:
        return None
    keys = ("order_id", "phone", "created_ts", "sent1_at", "sent2_at", "paid", "recovered")
    return dict(zip(keys, r))


def recovery_ensure(order_id, phone, created_ts):
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO recovery(order_id, phone, created_ts, updated_at) VALUES (?,?,?,?)",
            (order_id, str(phone), created_ts, time.time()),
        )
        _conn.commit()


def recovery_mark_sent(order_id, stage):
    col = "sent1_at" if stage == 1 else "sent2_at"
    with _lock:
        _conn.execute(f"UPDATE recovery SET {col}=?, updated_at=? WHERE order_id=?",
                      (time.time(), time.time(), order_id))
        _conn.commit()


def recovery_mark_paid(order_id, amount):
    with _lock:
        _conn.execute("UPDATE recovery SET paid=1, recovered=?, updated_at=? WHERE order_id=?",
                      (float(amount or 0), time.time(), order_id))
        _conn.commit()


def recovery_reset_unpaid():
    """ردیف‌های پرداخت‌نشده را پاک می‌کند (هنگام تغییرِ حالتِ test↔live تا مرحله‌ها قاطی نشوند)."""
    with _lock:
        _conn.execute("DELETE FROM recovery WHERE paid=0")
        _conn.commit()


def recovery_active(since_ts):
    """ردیف‌هایی که پیامی برایشان رفته، هنوز paid نشده‌اند و اخیرند — برای بازبینیِ پرداخت."""
    with _lock:
        cur = _conn.execute(
            "SELECT order_id, phone FROM recovery WHERE paid=0 AND sent1_at IS NOT NULL AND created_ts>=?",
            (since_ts,),
        )
        return cur.fetchall()


def recovery_stats(since_ts):
    with _lock:
        r = _conn.execute(
            "SELECT COUNT(*) , SUM(CASE WHEN sent1_at IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(paid), SUM(recovered) FROM recovery WHERE created_ts>=?",
            (since_ts,),
        ).fetchone()
    return {"orders": r[0] or 0, "messaged": r[1] or 0, "recovered_count": r[2] or 0, "recovered_amount": r[3] or 0}


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


def set_order_modified(order_id, date_modified):
    with _lock:
        _conn.execute("UPDATE orders SET date_modified=? WHERE order_id=?", (date_modified, order_id))
        _conn.commit()


def orders_modified_map():
    """{order_id: date_modified} برای سفارش‌های پست‌شده — برای تشخیصِ تغییر بدونِ فچِ detail."""
    with _lock:
        cur = _conn.execute("SELECT order_id, date_modified FROM orders WHERE message_id IS NOT NULL")
        return {r[0]: r[1] for r in cur.fetchall()}


def log_wc_sync(endpoint, pages, items, requests, duration, error=""):
    with _lock:
        _conn.execute(
            "INSERT INTO wc_sync_log(endpoint,pages,items,requests,duration_ms,error,ts) VALUES (?,?,?,?,?,?,?)",
            (endpoint, int(pages or 0), int(items or 0), int(requests or 0), int((duration or 0) * 1000), error or "", time.time()),
        )
        # فقط ۵۰۰ ردیفِ آخر نگه‌دار
        _conn.execute("DELETE FROM wc_sync_log WHERE id < (SELECT MAX(id)-500 FROM wc_sync_log)")
        _conn.commit()


def wc_sync_summary(since_ts):
    """جمعِ درخواست‌ها/آیتم‌ها/خطاها از since_ts — برای نمایشِ نرخ."""
    with _lock:
        r = _conn.execute(
            "SELECT COUNT(*), SUM(requests), SUM(items), SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END) "
            "FROM wc_sync_log WHERE ts>=?",
            (since_ts,),
        ).fetchone()
    return {"syncs": r[0] or 0, "requests": r[1] or 0, "items": r[2] or 0, "errors": r[3] or 0}
