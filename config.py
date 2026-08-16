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
# مدیرِ اصلی (Workstream H). اگر تنظیم نشود (۰)، رفتارِ فعلیِ همهٔ ادمین‌ها بدونِ تغییر می‌ماند (backward-compatible).
WT_PRIMARY_ADMIN_ID = _int("WT_PRIMARY_ADMIN_ID", 0)
# مقصدِ گزارش‌های مدیریتی (خلاصهٔ فروش/جمع‌بندیِ شیفت). خالی = پیویِ تک‌تکِ ادمین‌ها
REPORTS_CHAT_ID = _int("REPORTS_CHAT_ID", 0)

# ---------- ووکامرس ----------
WOO_URL = (_get("WOO_URL", "") or "").rstrip("/")
WOO_CK = _get("WOO_CK", "")
WOO_CS = _get("WOO_CS", "")
WOO_WEBHOOK_SECRET = _get("WOO_WEBHOOK_SECRET", "")
# کلیدِ اختیاریِ «نوشتن» (اگر خواستیم خواندن‌ها روی کلیدِ فقط‌خواندنی بمانند). خالی = همان WOO_CK/WOO_CS.
WOO_WRITE_CK = _get("WOO_WRITE_CK", "")
WOO_WRITE_CS = _get("WOO_WRITE_CS", "")

# ---------- سینکِ قیمت/موجودی از کانالِ CAT Group (wt_pricesync) ----------
WT_PRICESYNC_ENABLED = _bool("WT_PRICESYNC_ENABLED", False)              # کلیدِ اصلی (fail-closed)
WT_PRICESYNC_APPLY = _bool("WT_PRICESYNC_APPLY", False)                  # False=فقط گزارش، True=نوشتنِ واقعی
WT_PRICESYNC_OPERATOR_ID = _int("WT_PRICESYNC_OPERATOR_ID", 0)   # اپراتور — مقصدِ اکسلِ گزارش
WT_PRICESYNC_MIN_REFS = _int("WT_PRICESYNC_MIN_REFS", 100)               # رفرنسِ کانال < این → مشکوک، ننویس
WT_PRICESYNC_MAX_OOS = _int("WT_PRICESYNC_MAX_OOS", 60)                  # →ناموجود > این → نگه‌دار، هشدار
WT_PRICESYNC_MAX_CHANGES = _int("WT_PRICESYNC_MAX_CHANGES", 400)         # سقفِ کلِ تغییرات → نگه‌دار
WT_PRICESYNC_MAX_AGE_H = _int("WT_PRICESYNC_MAX_AGE_H", 48)              # اسنپ‌شاتِ کهنه‌تر از این ساعت → ننویس

# ---------- «حساب مالی» (نمای مالیِ CRM) ----------
WT_FINANCE_FIXED_SALARY = _int("WT_FINANCE_FIXED_SALARY", 300_000_000)   # حقوقِ ثابتِ ماهانه، ریال (۳۰ میلیون تومان)
WT_FINANCE_BUCKET = _int("WT_FINANCE_BUCKET", 0)                         # ایندکسِ باکت (فقط «جواهرتایم» = ۰)
# مبنای صفرِ زنجیرهٔ مانده: حساب در پایانِ اردیبهشت ۱۴۰۵ صفر شد، پس زنجیره از خرداد ۱۴۰۵ (carry=0) شروع می‌شود.
WT_FINANCE_CARRY_BASELINE = _get("WT_FINANCE_CARRY_BASELINE", "1405-03")

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
# نگاشت روش‌های پرداختِ خاص به نام نمایشی (مثلاً «دیگر/سایر» = حساب مالی مجموعه)
PAYMENT_ALIASES = {}

# ---------- مغزِ اختصاصیِ گزارشِ کار (ارزیابیِ AI) ----------
OPENAI_API_KEY = _get("OPENAI_API_KEY", "")
WT_MODEL = _get("WT_MODEL", "gpt-5.5")
# fallbackِ سقفِ خروجی برای featureهای بی‌تعریف در policy (پیش‌تر مصرف‌نشده بود؛ اکنون به مسیرِ اجرا وصل است).
# روی featureهای دارای مقدارِ صریح در WT_MODEL_POLICY اثر ندارد → رفتارِ فعلی حفظ می‌شود.
WT_MAX_TOKENS = _int("WT_MAX_COMPLETION_TOKENS", 2000)

# ---------- سیاستِ مدل به تفکیکِ feature (فاز صفر: کنترل + اندازه‌گیری؛ پیش‌فرض‌ها = رفتارِ فعلی) ----------
# هر feature: model / max_output_tokens / reasoning effort (فقط اگر مدل پشتیبانی کند) / timeout ثانیه.
# مقادیرِ پیش‌فرض عیناً همان اعداد سخت‌کدِ فعلی‌اند تا رفتار تغییر نکند (به‌جز افزودنِ effort=low به دو مسیرِ
# manager_reply/issue_routing که قبلاً بدونِ effort بودند و روی مدلِ استدلالی خروجیِ خالی می‌دادند — رفعِ باگِ F3).
_VALID_EFFORT = (None, "minimal", "low", "medium", "high")
WT_MODEL_POLICY = {
    "task_followup":            {"max_output_tokens": 1400,  "effort": "low",  "timeout": 90},
    "task_evaluate":            {"max_output_tokens": 2800,  "effort": "low",  "timeout": 120},
    "manager_reply":            {"max_output_tokens": 600,   "effort": "low",  "timeout": 60},
    "issue_routing":            {"max_output_tokens": 800,   "effort": "low",  "timeout": 60},
    "ig_content_plan":          {"max_output_tokens": 12000, "effort": "high", "timeout": 600},
    "ig_content_plan_ondemand": {"max_output_tokens": 12000, "effort": "high", "timeout": 600},
}


def wt_policy(feature: str) -> dict:
    """سیاستِ اعتبارسنجی‌شدهٔ یک feature. مقادیرِ نامعتبر/صفر/منفی/خیلی‌بزرگ → safe default. مدل هرگز حدس‌زده نمی‌شود."""
    p = WT_MODEL_POLICY.get(feature) or {}
    model = p.get("model") or WT_MODEL
    try:
        mx = int(p.get("max_output_tokens") or WT_MAX_TOKENS)
    except (TypeError, ValueError):
        mx = WT_MAX_TOKENS
    if mx <= 0 or mx > 200000:                 # صفر/منفی/بی‌معنا → fallback امن
        mx = WT_MAX_TOKENS if 0 < WT_MAX_TOKENS <= 200000 else 2000
    effort = p.get("effort")
    if effort not in _VALID_EFFORT:            # فقط مقدارِ پشتیبانی‌شده
        effort = None
    try:
        to = float(p.get("timeout") or 90)
    except (TypeError, ValueError):
        to = 90.0
    if to <= 0 or to > 900:                     # پنجرهٔ امنِ timeout
        to = 90.0
    return {"feature": feature, "model": model, "max_output_tokens": mx, "effort": effort, "timeout": to}

# ---------- نسخهٔ عملیاتیِ اصلی (Core Operational Release) — feature flags ----------
# همهٔ پیش‌فرض‌ها backward-compatible: خاموش = رفتارِ دقیقاً فعلیِ open/done. فعال‌سازی گام‌به‌گام (WS20).
# configِ نامعتبر → fail-closed (خاموش). هیچ flag اجازهٔ دورزدنِ authorization را نمی‌دهد.
WT_LIFECYCLE_ENABLED = _bool("WT_LIFECYCLE_ENABLED", False)                    # چرخهٔ کاملِ تسکِ انسانی
WT_MANAGER_VERIFICATION_ENABLED = _bool("WT_MANAGER_VERIFICATION_ENABLED", False)  # حالتِ تأییدِ مدیر
WT_AUTOMATIC_VERIFICATION_ENABLED = _bool("WT_AUTOMATIC_VERIFICATION_ENABLED", False)  # صحت‌سنجیِ قطعیِ API
WT_WEBSITE_TASKS_ENABLED = _bool("WT_WEBSITE_TASKS_ENABLED", False)            # تسک‌های مرتبط با سایت
WT_INSTAGRAM_TASKS_ENABLED = _bool("WT_INSTAGRAM_TASKS_ENABLED", False)        # تسک‌های انسانیِ اینستاگرام
WT_NEW_NOTIFICATIONS_ENABLED = _bool("WT_NEW_NOTIFICATIONS_ENABLED", False)    # نوتیفیکیشن‌های جدیدِ چرخه
# ساختِ خودکارِ تسک برای پرسنل (خزشِ روزانه + پلنِ خودکارِ اینستاگرام). خاموش = فقط گزارشِ کار جمع می‌شود، تسک ساخته نمی‌شود.
WT_AUTO_TASKS_ENABLED = _bool("WT_AUTO_TASKS_ENABLED", False)
# تأمین‌کنندهٔ سیتیزن (app.supplier.example) — موبایلِ لاگینِ OTP. کدِ پیامکی از طریقِ دکمهٔ ربات وارد می‌شود.
WT_SAATI_MOBILE = _get("WT_SAATI_MOBILE", "")
WT_CITIZEN_ENABLED = _bool("WT_CITIZEN_ENABLED", False)   # کلیدِ اصلیِ جابِ روزانهٔ سیتیزن (fail-closed)
WT_CITIZEN_APPLY = _bool("WT_CITIZEN_APPLY", False)        # False=فقط گزارش، True=نوشتنِ واقعی
WT_CITIZEN_OPERATOR_ID = _int("WT_CITIZEN_OPERATOR_ID", 0)  # اپراتور — مقصدِ گزارش
WT_CITIZEN_MAX_OOS = _int("WT_CITIZEN_MAX_OOS", 200)       # اگر →ناموجود > این → نگه‌دار، هشدار (ضدِ فاجعه)

# درجِ خودکارِ تصاویر (کتابخانهٔ رسانه → محصولاتِ بی‌عکس، بر اساسِ رفرنس)
WT_MEDIAIMG_APPLY = _bool("WT_MEDIAIMG_APPLY", False)      # False=فقط گزارشِ dry-run، True=نوشتنِ واقعی
WT_MEDIAIMG_SCAN_PAGES = _int("WT_MEDIAIMG_SCAN_PAGES", 25)  # چند صفحهٔ ۱۰۰تاییِ اخیر برای یافتنِ بی‌عکس‌ها
WT_MEDIAIMG_USE_CRM = _bool("WT_MEDIAIMG_USE_CRM", True)   # اگر اندپوینتِ CRM هست، متادیتای کامل (شاملِ description)
WT_MEDIAIMG_OPERATOR_ID = _int("WT_MEDIAIMG_OPERATOR_ID", 0)  # اپراتور — می‌تواند دکمه را بزند + مقصدِ گزارش

# نگاشتِ config-drivenِ «مسئولِ سایت» و «مسئولِ اینستاگرام» (Telegram user id). ۰ = تعیین‌نشده.
# اگر ۰ باشد، مسیرهای موجودِ meta (wp_link / ig_admin_uid) به‌عنوانِ fallback استفاده می‌شوند (backward-compatible).
WT_WEBSITE_ASSIGNEE_ID = _int("WT_WEBSITE_ASSIGNEE_ID", 0)
WT_INSTAGRAM_ASSIGNEE_ID = _int("WT_INSTAGRAM_ASSIGNEE_ID", 0)

# سقف‌های هزینه/ایمنیِ صحت‌سنجیِ API (WS18): timeoutِ کوتاه، retry/pollingِ کران‌دار، cacheِ کوتاه.
WT_VERIFY_TIMEOUT_SEC = float(_int("WT_VERIFY_TIMEOUT_SEC", 8))
WT_VERIFY_MAX_ATTEMPTS = _int("WT_VERIFY_MAX_ATTEMPTS", 3)
WT_VERIFY_CACHE_TTL_SEC = _int("WT_VERIFY_CACHE_TTL_SEC", 60)

# ---------- مدیریتِ سادهٔ پرسنل + حضور + حقوقِ ساده (همه پیش‌فرض خاموش؛ فعال‌سازیِ مرحله‌ای) ----------
WT_PERSONNEL_ENABLED = _bool("WT_PERSONNEL_ENABLED", False)          # مدیریتِ پرسنل (افزودن/ویرایش/فعال‌سازی)
WT_ATTENDANCE_ENABLED = _bool("WT_ATTENDANCE_ENABLED", False)        # ثبتِ ورود/خروج (روی همان مسیرِ گزارش)
WT_SIMPLE_PAYROLL_ENABLED = _bool("WT_SIMPLE_PAYROLL_ENABLED", False)  # محاسبهٔ سادهٔ حقوق (بدونِ payroll engine پیچیده)
# واحدِ پولِ حقوق: مبلغ‌ها به‌صورتِ عددِ صحیح در همین واحد ذخیره می‌شوند (تبدیل یک‌بار؛ در محاسبات مخلوط نشود).
WT_SALARY_UNIT = (_get("WT_SALARY_UNIT", "toman") or "toman").strip().lower()  # toman | rial


# ---------- وب‌هوک ----------
WEBHOOK_ENABLED = _bool("WEBHOOK_ENABLED", False)
WEBHOOK_HOST = _get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _int("WEBHOOK_PORT", 8088)
WEBHOOK_PATH = _get("WEBHOOK_PATH", "/woo/order")

# ---------- ذخیره‌سازی ----------
DB_PATH = _get("DB_PATH", "data/orderbot.db")
# مهلتِ انتظار روی «database is locked» (میلی‌ثانیه). WAL عمداً فعال نمی‌شود (سازگاری با backup/deployment فعلی).
SQLITE_BUSY_TIMEOUT_MS = _int("SQLITE_BUSY_TIMEOUT_MS", 5000)
