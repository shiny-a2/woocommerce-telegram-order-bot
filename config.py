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
# مقصدِ گزارش‌های مدیریتی (خلاصهٔ فروش/جمع‌بندیِ شیفت). خالی = پیویِ تک‌تکِ ادمین‌ها
REPORTS_CHAT_ID = _int("REPORTS_CHAT_ID", 0)

# ---------- ووکامرس ----------
WOO_URL = (_get("WOO_URL", "") or "").rstrip("/")
WOO_CK = _get("WOO_CK", "")
WOO_CS = _get("WOO_CS", "")
WOO_WEBHOOK_SECRET = _get("WOO_WEBHOOK_SECRET", "")

# ---------- بازیابیِ پرداختِ ناموفق (پیام به مشتری از طریقِ یوزربات) ----------
RECOVERY_MODE = (_get("RECOVERY_MODE", "off") or "off").strip().lower()  # off | test | live
RECOVERY_TEST_PHONE = _get("RECOVERY_TEST_PHONE", "")  # در حالتِ test همه‌ی پیام‌ها به این شماره می‌رود
RECOVERY_STATUSES = _csv("RECOVERY_STATUSES", "failed,pending")
RECOVERY_FIRST_DELAY_MIN = _int("RECOVERY_FIRST_DELAY_MIN", 30)  # پیامِ اول چند دقیقه بعد از رهاشدن
RECOVERY_SECOND_DELAY_H = _int("RECOVERY_SECOND_DELAY_H", 24)    # پیامِ دوم چند ساعت بعد از اولی
RECOVERY_WINDOW_H = _int("RECOVERY_WINDOW_H", 48)               # فقط سفارش‌های این بازه
RECOVERY_SEND_START = _int("RECOVERY_SEND_START", 10)           # ساعتِ مجازِ ارسال به مشتری (تهران)
RECOVERY_SEND_END = _int("RECOVERY_SEND_END", 21)
RECOVERY_MAX_PER_TICK = _int("RECOVERY_MAX_PER_TICK", 8)        # سقفِ پیام در هر چرخه (ضدِسیلِ بک‌لاگ)
# اتصال به صفِ تراکنشیِ یوزرباتِ tg-outreach
TXOUT_URL = _get("TXOUT_URL", "http://127.0.0.1:8091/api/tx")
TXOUT_TOKEN = _get("TXOUT_TOKEN", "")  # همان DASH_TOKEN یوزربات


# ---------- CRM (افزونه‌ی a2-crm-plugin، REST اختصاصیِ تلگرام) ----------
# پایه‌ی REST که خودِ افزونه می‌دهد، شاملِ «…/wp-json/a2crm/v1/tg»
CRM_TG_URL = (_get("CRM_TG_URL", "") or "").rstrip("/")
# توکنِ اختصاصیِ تلگرام (هدر X-A2-Token). تا ست نشود، بخشِ CRM در ربات خاموش است
CRM_TG_TOKEN = _get("CRM_TG_TOKEN", "")

# ---------- رفتار ----------
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", 60)
POST_STATUSES = _csv("POST_STATUSES")
PAID_STATUSES = _csv("PAID_STATUSES", "processing,completed")
NOTE_LOOKBACK_DAYS = _int("NOTE_LOOKBACK_DAYS", 14)
MAX_PHOTOS = _int("MAX_PHOTOS", 10)
CURRENCY_LABEL = _get("CURRENCY_LABEL", "تومان")
# نامِ نمایشیِ فروشگاه در پیام‌های مشتری (در .env مقدارِ واقعی را بگذار)
SHOP_NAME = _get("SHOP_NAME", "گالری")
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
