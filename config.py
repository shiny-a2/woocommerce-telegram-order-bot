"""پیکربندی متمرکز که از فایل .env خوانده می‌شود."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None):
    return os.getenv(name, default)


def _int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _bool(name, default=False):
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "بله")


def _csv(name, default=""):
    raw = os.getenv(name, default) or ""
    return [s for s in raw.replace(" ", "").split(",") if s]


def _id_list(name):
    out = []
    for part in _csv(name):
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


# ---------- تلگرام ----------
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = _int("TELEGRAM_GROUP_ID", 0)
FOLLOWUP_GROUP_ID = _int("FOLLOWUP_GROUP_ID", 0)  # گروهِ پیگیریِ سفارش‌های رهاشده
ADMIN_USER_IDS = _id_list("ADMIN_USER_IDS")

# ---------- ووکامرس ----------
WOO_URL = (_get("WOO_URL", "") or "").rstrip("/")
WOO_CK = _get("WOO_CK", "")
WOO_CS = _get("WOO_CS", "")
WOO_WEBHOOK_SECRET = _get("WOO_WEBHOOK_SECRET", "")

# ---------- رفتار ----------
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", 60)
POST_STATUSES = _csv("POST_STATUSES")
PAID_STATUSES = _csv("PAID_STATUSES", "processing,completed")
NOTE_LOOKBACK_DAYS = _int("NOTE_LOOKBACK_DAYS", 14)
MAX_PHOTOS = _int("MAX_PHOTOS", 10)
CURRENCY_LABEL = _get("CURRENCY_LABEL", "تومان")
# واحد فروشگاه ریال است؛ برای نمایش تومان مبلغ بر این عدد تقسیم می‌شود (۱۰). برای نمایش ریال ۱ بگذار
MONEY_DIVISOR = _int("MONEY_DIVISOR", 10)
# نگاشت روش‌های پرداختِ خاص به نام نمایشی دلخواه (نمونه: {"other": "Bank transfer"})
PAYMENT_ALIASES = {}

# ---------- وب‌هوک ----------
WEBHOOK_ENABLED = _bool("WEBHOOK_ENABLED", False)
WEBHOOK_HOST = _get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _int("WEBHOOK_PORT", 8088)
WEBHOOK_PATH = _get("WEBHOOK_PATH", "/woo/order")

# ---------- ذخیره‌سازی ----------
DB_PATH = _get("DB_PATH", "data/orderbot.db")
