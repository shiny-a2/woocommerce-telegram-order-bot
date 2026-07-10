"""کلاینتِ CRM یاقوت (افزونه‌ی a2-crm-plugin) از طریقِ REST اختصاصیِ تلگرام.

پایه‌ی آدرس (config.CRM_TG_URL) خودش شاملِ «…/a2crm/v1/tg» است؛ پس مسیرها
نسبی‌اند: /ping /profile /agents /lead-status /note /assign /update .

فاز ۱ = خواندن (get_profile / get_agents / ping) فعال است.
فاز ۲ = نوشتن (set_status / add_note / assign / update_fields) آماده است ولی فقط
بعد از تأییدِ فاز ۱ در رابطِ کاربری وصل می‌شود.

هیچ سکرتی لاگ نمی‌شود. تا CRM_TG_URL و CRM_TG_TOKEN ست نشوند، enabled() مقدار
False می‌دهد و رابط، بخشِ CRM را نشان نمی‌دهد.
"""
from __future__ import annotations

import asyncio

import requests

import config

_TIMEOUT = (5, 15)  # (connect, read): dropِ اتصال در ۵ث سریع fail و retry؛ پاسخِ کند تا ۱۵ث مهلت
_RETRIES = 2        # تلاشِ مجدد روی dropِ متناوبِ اتصالِ سایت (تا کارت‌های گروه پایدار شوند)


def enabled() -> bool:
    return bool(config.CRM_TG_URL and config.CRM_TG_TOKEN)


def _headers() -> dict:
    return {"X-A2-Token": config.CRM_TG_TOKEN, "Accept": "application/json"}


def normalize_phone(raw) -> str:
    """به فرمتِ ۱۱رقمیِ «۰۹xxxxxxxxx» نرمال می‌کند (تحملِ +98/0098/۹۸/فاصله)."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    return digits


_SOURCE_FA = {
    "webchat": "چت سایت",
    "website_popup": "پاپ‌آپ سایت",
    "inperson_form": "مشتری حضوری",
    "telegram_bot": "ربات تلگرام",
    # منابعِ محتملِ دیگر (اگر افزونه اضافه کند)
    "instagram": "اینستاگرام",
    "whatsapp": "واتساپ",
    "woocommerce": "سفارشِ سایت",
    "order": "سفارشِ سایت",
    "manual": "ثبتِ دستی",
    "site": "سایت",
    "chat": "چت سایت",
    "popup": "پاپ‌آپ سایت",
    "inperson": "مشتری حضوری",
    "phone": "تماسِ تلفنی",
}


def source_label(source) -> str:
    """منبعِ لید (انگلیسی) → برچسبِ فارسی. ناشناخته = همان مقدارِ خام (تا چیزی گم نشود)."""
    raw = str(source or "").strip()
    if not raw:
        return "—"
    return _SOURCE_FA.get(raw.lower(), raw)


def _parse(r):
    """بدنه‌ی JSON را برمی‌گرداند حتی با وضعیتِ 4xx، اگر شاملِ کلیدِ ok باشد.

    CRM از کدِ وضعیتِ HTTP معنایی استفاده می‌کند (مثلاً 404 = contact_not_found با بدنه‌ی
    {"ok":false,"msg":...})؛ پس به‌جای raise، بدنه را برمی‌گردانیم تا فراخوان ok را بررسی کند.
    خطاهای واقعی (۵xx یا بدونِ JSON) همچنان raise می‌شوند.
    """
    try:
        data = r.json()
    except Exception:
        r.raise_for_status()
        raise
    if isinstance(data, dict) and "ok" in data:
        return data
    r.raise_for_status()
    return data


def _do(method, path, **kw):
    """درخواست با retry روی ConnectTimeout/ConnectionError (dropِ متناوبِ سایت جبران شود)."""
    import time as _t
    last = None
    for attempt in range(_RETRIES + 1):
        try:
            return _parse(method(f"{config.CRM_TG_URL}{path}", headers=_headers(), timeout=_TIMEOUT, **kw))
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            last = e
            if attempt < _RETRIES:
                _t.sleep(0.4 * (attempt + 1))
    raise last


# ---------- خواندن (فاز ۱) ----------
def _get_sync(path: str, params: dict | None = None):
    return _do(requests.get, path, params=params or {})


async def ping() -> dict:
    """تستِ سلامت + احراز توکن."""
    return await asyncio.to_thread(_get_sync, "/ping")


async def get_profile(phone, limit: int = 15) -> dict:
    """کارتِ یکپارچه‌ی مشتری+لید بر اساسِ موبایل (contact + meta + lead + notes + status_log + flags)."""
    return await asyncio.to_thread(_get_sync, "/profile", {"phone": normalize_phone(phone), "limit": limit})


async def get_agents() -> list:
    """روسترِ مسئول‌های قابلِ‌اساین: [{user_id, display_name}]."""
    data = await asyncio.to_thread(_get_sync, "/agents")
    return data.get("agents", []) if isinstance(data, dict) else (data or [])


async def viewed_products(phone, limit=20) -> list:
    """محصولاتِ مشاهده‌شده‌ی مشتری (ردیابیِ CRM): [{product, product_id?, url?, viewed_local, count?}]."""
    data = await asyncio.to_thread(_get_sync, "/viewed", {"phone": normalize_phone(phone), "limit": limit})
    return data.get("viewed", []) if isinstance(data, dict) else (data or [])


async def recommend(phone, limit=8) -> list:
    """محصولاتِ پیشنهادی برای مشتری (موتورِ پیشنهادِ CRM).

    قالبِ موردِانتظار: {"ok":true,"items":[{product, product_id?, url?, price?, reason?}]}
    """
    data = await asyncio.to_thread(_get_sync, "/recommend", {"phone": normalize_phone(phone), "limit": limit})
    if isinstance(data, dict):
        return data.get("items") or data.get("recommend") or data.get("products") or []
    return data or []


async def new_leads(since_id=0, limit=50) -> dict:
    """لیدهای جدیدِ ثبت‌شده پس از since_id (مرتب بر اساس id صعودی).

    خروجی: {"leads":[{id,phone,name,status,status_label,source,assigned_to,assigned_name,created_local}], "max_id":N}
    """
    data = await asyncio.to_thread(_get_sync, "/new-leads", {"since_id": since_id, "limit": limit})
    if isinstance(data, dict):
        return data
    return {"leads": data or [], "max_id": 0}


async def activity(user_id, date_from, date_to) -> dict:
    """آمارِ فعالیتِ per-userِ ثبت‌شده در سایت (اندپوینتِ /activity افزونه، group=1).

    خروجی: {"ok":true,"user_id","user_login","scope","counts":{action:count},"by_object":{type:count},"total"}.
    برای صحت‌سنجیِ ادعای پرسنل (مثلاً «۱۰۰ محصول دسته‌بندی کردم») با کارِ واقعیِ ثبت‌شده.
    """
    return await asyncio.to_thread(
        _get_sync, "/activity", {"user_id": user_id, "from": date_from, "to": date_to, "group": 1})


async def due_leads(before=None, after=None, assigned_to=None, limit=50) -> list:
    """لیدهای سررسیدشده برای یادآوری: [{phone,name,status,status_label,next_follow_up,assigned_name,...}].

    `after` مرزِ پایین است (فقط سررسیدهای اخیر) — اگر سمتِ سرور پشتیبانی شود فیلتر می‌کند،
    وگرنه نادیده گرفته می‌شود و فیلترِ محلیِ پولر جلوِ انبارِ قدیمی را می‌گیرد.
    """
    params = {"limit": limit}
    if before:
        params["before"] = before
    if after:
        params["after"] = after
    if assigned_to:
        params["assigned_to"] = assigned_to
    data = await asyncio.to_thread(_get_sync, "/due", params)
    return data.get("due", []) if isinstance(data, dict) else (data or [])


# ---------- نوشتن (فاز ۲ — آماده، در UI بعداً وصل می‌شود) ----------
def _post_sync(path: str, payload: dict):
    return _do(requests.post, path, json=payload)


async def set_status(phone, status, actor_name, follow_up_at=None, note=None, **extra) -> dict:
    payload = {"phone": normalize_phone(phone), "status": status, "actor_name": actor_name}
    if follow_up_at:
        payload["follow_up_at"] = follow_up_at
    if note:
        payload["note"] = note
    payload.update({k: v for k, v in extra.items() if v is not None})  # other_site / unavailable_product
    return await asyncio.to_thread(_post_sync, "/lead-status", payload)


async def add_note(phone, note, actor_name) -> dict:
    return await asyncio.to_thread(
        _post_sync, "/note", {"phone": normalize_phone(phone), "note": note, "actor_name": actor_name}
    )


async def assign(phone, user_id, actor_name) -> dict:
    return await asyncio.to_thread(
        _post_sync, "/assign", {"phone": normalize_phone(phone), "user_id": user_id, "actor_name": actor_name}
    )


async def update_fields(phone, entity, fields: dict, actor_name) -> dict:
    """آپدیتِ تک‌فیلدِ امن (partial). entity = 'lead' یا 'contact'."""
    return await asyncio.to_thread(
        _post_sync,
        "/update",
        {"phone": normalize_phone(phone), "entity": entity, "fields": fields, "actor_name": actor_name},
    )
