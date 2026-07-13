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

# ---------- بهینه‌سازیِ خواندنِ ووکامرس (sync افزایشی) ----------
WC_INCREMENTAL = (_get("WC_INCREMENTAL", "on") or "on").strip().lower() != "off"  # off = مسیرِ قدیمیِ full-scan
WC_OVERLAP_MIN = _int("WC_OVERLAP_MIN", 5)             # overlap امن برای جانیفتادنِ سفارش
WC_SYNC_BACKFILL_H = _int("WC_SYNC_BACKFILL_H", 48)    # اولین sync / نبودِ last_sync
WC_EDIT_FRESH_HOURS = _int("WC_EDIT_FRESH_HOURS", 6)   # سفارش‌های تازه‌تر از این، برای گرفتنِ نوت هر دور رفرش شوند
WC_MAX_RETRY = _int("WC_MAX_RETRY", 3)                 # retry با backoff (concurrency/breaker در woo.py: WOO_* getattr)


# ---------- CRM (افزونه‌ی a2-crm-plugin، REST اختصاصیِ تلگرام) ----------
# پایه‌ی REST که خودِ افزونه می‌دهد، شاملِ «…/wp-json/a2crm/v1/tg»
CRM_TG_URL = (_get("CRM_TG_URL", "") or "").rstrip("/")
# توکنِ اختصاصیِ تلگرام (هدر X-A2-Token). تا ست نشود، بخشِ CRM در ربات خاموش است
CRM_TG_TOKEN = _get("CRM_TG_TOKEN", "")

# ---------- آنالیزِ اینستاگرام ----------
# API محلیِ فقط‌خواندنیِ سرویسِ اینستاگرامِ لاگین‌شده (صاحبِ تنها سشنِ مجاز). این بات هرگز خودش
# لاگین/سشن نمی‌زند؛ فقط از این API می‌خواند و روی دادهٔ ذخیره‌شده آنالیز می‌کند (ضدِ بن/چالش).
IG_DASH_URL = (_get("IG_DASH_URL", "") or "").rstrip("/")
IG_DASH_TOKEN = _get("IG_DASH_TOKEN", "")
# (منسوخ) سرویسِ قدیمیِ ig-insights با سشنِ کپی — دیگر استفاده نمی‌شود (خطرِ سشنِ دوم).
IG_INSIGHTS_URL = (_get("IG_INSIGHTS_URL", "") or "").rstrip("/")
IG_INSIGHTS_TOKEN = _get("IG_INSIGHTS_TOKEN", "")

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
# نگاشت روش‌های پرداختِ خاص به نام نمایشی (در پیکربندیِ خصوصی مقداردهی می‌شود)
PAYMENT_ALIASES = {}

# ---------- مغزِ اختصاصیِ گزارشِ کار (ارزیابیِ AI) ----------
OPENAI_API_KEY = _get("OPENAI_API_KEY", "")
WT_MODEL = _get("WT_MODEL", "gpt-5.5")
WT_MAX_TOKENS = _int("WT_MAX_COMPLETION_TOKENS", 2000)

# ---------- وب‌هوک ----------
WEBHOOK_ENABLED = _bool("WEBHOOK_ENABLED", False)
WEBHOOK_HOST = _get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _int("WEBHOOK_PORT", 8088)
WEBHOOK_PATH = _get("WEBHOOK_PATH", "/woo/order")

# ---------- ذخیره‌سازی ----------
DB_PATH = _get("DB_PATH", "data/orderbot.db")
