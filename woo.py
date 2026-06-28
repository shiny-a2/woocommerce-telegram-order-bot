"""کلاینت رابط ووکامرس (کتابخانه‌ی همگام، پیچیده‌شده برای asyncio)."""
from __future__ import annotations

import asyncio

from woocommerce import API

import config

_api = None

# جدول کامل استان‌های ایران (کد افزونه → نام). نسخه‌ی زنده از خود ووکامرس
# با load_states() گرفته می‌شود؛ این جدول فقط پشتیبان است اگر API در دسترس نبود.
_STATES_FALLBACK = {
    "ABZ": "البرز", "ADL": "اردبیل", "EAZ": "آذربایجان شرقی", "WAZ": "آذربایجان غربی",
    "BHR": "بوشهر", "CHB": "چهارمحال و بختیاری", "FRS": "فارس", "GIL": "گیلان",
    "GLS": "گلستان", "HDN": "همدان", "HRZ": "هرمزگان", "ILM": "ایلام",
    "ESF": "اصفهان", "KRN": "کرمان", "KRH": "کرمانشاه", "NKH": "خراسان شمالی",
    "RKH": "خراسان رضوی", "SKH": "خراسان جنوبی", "KHZ": "خوزستان",
    "KBD": "کهگیلویه و بویراحمد", "KRD": "کردستان", "LRS": "لرستان", "MKZ": "مرکزی",
    "MZN": "مازندران", "GZN": "قزوین", "QHM": "قم", "SMN": "سمنان",
    "SBN": "سیستان و بلوچستان", "THR": "تهران", "YZD": "یزد", "ZJN": "زنجان",
}
_STATES = None  # نسخه‌ی زنده از API


def _client():
    global _api
    if _api is None:
        _api = API(
            url=config.WOO_URL,
            consumer_key=config.WOO_CK,
            consumer_secret=config.WOO_CS,
            version="wc/v3",
            timeout=30,
            # کلید/سکرت در هدرِ Basic می‌رود نه در URL — تا در خطاها/لاگ لو نرود
            query_string_auth=False,
        )
    return _api


def _get_sync(endpoint, params=None):
    resp = _client().get(endpoint, params=params or {})
    resp.raise_for_status()
    return resp.json()


async def get(endpoint, params=None):
    return await asyncio.to_thread(_get_sync, endpoint, params)


def _put_sync(endpoint, data):
    resp = _client().put(endpoint, data)
    resp.raise_for_status()
    return resp.json()


async def put(endpoint, data):
    return await asyncio.to_thread(_put_sync, endpoint, data)


def _post_sync(endpoint, data):
    resp = _client().post(endpoint, data)
    resp.raise_for_status()
    return resp.json()


async def post(endpoint, data):
    return await asyncio.to_thread(_post_sync, endpoint, data)


async def get_order(order_id: int):
    return await get(f"orders/{order_id}")


async def get_product(product_id: int):
    return await get(f"products/{product_id}")


async def get_notes(order_id: int):
    return await get(f"orders/{order_id}/notes")


async def list_recent_orders(per_page=20):
    # فقط شناسه و وضعیت لازم است (پردازشِ کامل جداگانه get_order می‌کند) → سبک و سریع
    return await get(
        "orders", {"per_page": per_page, "orderby": "id", "order": "desc", "_fields": "id,status,date_created"}
    )


async def search_orders(query, per_page=10):
    """جستجوی سفارش بر اساس نام، شماره تماس یا نام محصول (search ووکامرس همه را پوشش می‌دهد)."""
    return await get(
        "orders",
        {"search": query, "per_page": per_page, "orderby": "date", "order": "desc"},
    )


# فقط فیلدهای لازم برای گزارش‌ها → پیلودِ بسیار سبک‌تر و سریع‌تر
_RANGE_FIELDS = "id,number,status,total,payment_method_title,billing,shipping,line_items,date_created"


def _get_paged_sync(endpoint, params):
    resp = _client().get(endpoint, params=params or {})
    resp.raise_for_status()
    total = int(resp.headers.get("X-WP-TotalPages") or 1)
    return resp.json(), total


async def list_orders_in_range(after_iso, before_iso):
    """همه‌ی سفارش‌های یک بازه؛ صفحه‌ی اول برای تعداد صفحات، بقیه به‌صورت موازی."""
    base = {
        "after": after_iso, "before": before_iso, "per_page": 100,
        "orderby": "date", "order": "asc", "_fields": _RANGE_FIELDS,
    }
    first, total_pages = await asyncio.to_thread(_get_paged_sync, "orders", {**base, "page": 1})
    out = list(first)
    if total_pages > 1:
        rest = await asyncio.gather(*[
            asyncio.to_thread(_get_sync, "orders", {**base, "page": p})
            for p in range(2, total_pages + 1)
        ])
        for batch in rest:
            out.extend(batch)
    return out


async def load_states():
    """جدول کد→نام استان را از خود ووکامرس می‌گیرد و کش می‌کند."""
    global _STATES
    try:
        data = await get("data/countries/IR")
        m = {s.get("code"): s.get("name") for s in (data.get("states") or []) if s.get("code")}
        if m:
            _STATES = m
            print(f"[woo] جدول {len(m)} استان از فروشگاه بارگذاری شد.")
    except Exception as e:
        print(f"[woo] گرفتن جدول استان‌ها ناموفق بود، از جدول داخلی استفاده می‌شود: {e}")


def state_name(code):
    if not code:
        return ""
    table = _STATES if _STATES else _STATES_FALLBACK
    return table.get(code, code)


def _map_payment(title):
    return config.PAYMENT_ALIASES.get((title or "").strip(), title or "")


def caption_fields(order):
    """فیلدهای لازم برای کپشن را از یک سفارش بیرون می‌کشد."""
    b = order.get("billing", {}) or {}
    s = order.get("shipping", {}) or {}

    name = f"{b.get('first_name', '')} {b.get('last_name', '')}".strip()
    phone = b.get("phone", "") or s.get("phone", "")

    # اگر آدرس ارسال پر بود از آن استفاده کن، وگرنه آدرس صورت‌حساب
    src = s if s.get("address_1") else b
    addr_parts = [src.get("city", ""), src.get("address_1", ""), src.get("address_2", "")]
    address = "، ".join([p for p in addr_parts if p])

    ship_lines = order.get("shipping_lines") or []
    shipping = ship_lines[0].get("method_title", "") if ship_lines else ""

    return {
        "number": order.get("number") or order.get("id"),
        "name": name,
        "phone": phone,
        "province": state_name(src.get("state", "")),
        "address": address,
        "postcode": src.get("postcode", ""),
        "payment": _map_payment(order.get("payment_method_title", "")),
        "shipping": shipping,
        "total": order.get("total", ""),
        "date_created": order.get("date_created"),
    }
