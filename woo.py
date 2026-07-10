"""کلاینت رابط ووکامرس (کتابخانه‌ی همگام، پیچیده‌شده برای asyncio)."""
from __future__ import annotations

import asyncio
import random
import time as _time

import requests
from woocommerce import API

import config

_api = None
_req_count = 0  # شمارنده‌ی کلِ درخواست‌های ووکامرس (برای لاگِ نرخ)


def req_count() -> int:
    return _req_count

# ---------- نرخِ درخواست + circuit-breaker + شمارش (شفاف؛ رفتارِ فراخوان‌ها تغییر نمی‌کند) ----------
_MAX_CONC = int(getattr(config, "WOO_MAX_CONCURRENCY", 3))          # concurrencyِ پایین به سایت
_RETRY_STATUS = {0, 429, 500, 502, 503, 504}                        # 0 = خطای شبکه/تایم‌اوت
_BREAKER_TRIP = int(getattr(config, "WOO_BREAKER_FAILS", 5))        # چند شکستِ پشت‌سرهم → باز شدنِ بریکر
_BREAKER_COOLDOWN = int(getattr(config, "WOO_BREAKER_COOLDOWN_S", 180))
_sem = None
_breaker = {"fails": 0, "open_until": 0.0}
_counters = {"req": 0, "retries": 0, "breaker_skips": 0, "by_status": {}, "since": _time.time()}


def _semaphore():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_CONC)
    return _sem


def stats_snapshot(reset=True):
    """آمارِ درخواست‌های Woo از آخرین snapshot (برای logging نرخِ req/min)."""
    now = _time.time()
    snap = dict(_counters)
    snap["window_s"] = round(now - _counters["since"], 1)
    snap["by_status"] = dict(_counters["by_status"])
    if reset:
        _counters.update({"req": 0, "retries": 0, "breaker_skips": 0, "by_status": {}, "since": now})
    return snap


def breaker_open():
    return _time.time() < _breaker["open_until"]


async def _call(fn, *args):
    """هر درخواستِ ووکامرس: concurrency-limit + retry/backoff روی 429/5xx/تایم‌اوت + circuit-breaker + شمارش.

    رفتارِ خروجی مثلِ قبل است؛ روی خطای غیرقابلِ‌retry همان استثنا بالا می‌رود.
    """
    if breaker_open():
        _counters["breaker_skips"] += 1
        raise RuntimeError("woo_circuit_open")  # مدار باز → به سایت فشار نده
    retries = int(getattr(config, "WC_MAX_RETRY", 3))
    async with _semaphore():
        last = None
        for attempt in range(retries + 1):
            try:
                r = await asyncio.to_thread(fn, *args)
                _counters["req"] += 1
                _breaker["fails"] = 0
                return r
            except Exception as e:
                last = e
                code = (e.response.status_code
                        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None else 0)
                _counters["by_status"][code] = _counters["by_status"].get(code, 0) + 1
                if code not in _RETRY_STATUS or attempt >= retries:
                    break
                _counters["retries"] += 1
                await asyncio.sleep(min(8.0, 0.5 * (2 ** attempt)) + random.random() * 0.3)
        _breaker["fails"] += 1
        if _breaker["fails"] >= _BREAKER_TRIP:
            _breaker["open_until"] = _time.time() + _BREAKER_COOLDOWN
            _breaker["fails"] = 0
            print(f"[wc] circuit-breaker باز شد — {_BREAKER_COOLDOWN}s استراحت (سایت فشار نگیرد).")
        raise last

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
            # هاست هدرِ Authorization را حذف می‌کند (۴۰۱)، پس احراز از طریقِ query-string انجام می‌شود.
            # روی HTTPS این امن است (کلید/سکرت در بدنه‌ی رمزنگاری‌شده‌ی TLS می‌رود).
            query_string_auth=True,
        )
    return _api


def _get_sync(endpoint, params=None):
    global _req_count
    _req_count += 1
    resp = _client().get(endpoint, params=params or {})
    resp.raise_for_status()
    return resp.json()


async def get(endpoint, params=None):
    return await _call(_get_sync, endpoint, params)


def _count_sync(endpoint, params=None):
    """کلِ آیتم‌ها را از هدرِ X-WP-Total می‌خواند، بدونِ کشیدنِ همه‌ی صفحه‌ها."""
    global _req_count
    _req_count += 1
    p = dict(params or {})
    p["per_page"] = 1  # فقط یک آیتم؛ عددِ کل در هدر است
    resp = _client().get(endpoint, params=p)
    resp.raise_for_status()
    total = resp.headers.get("X-WP-Total")
    if total is None:  # پاسخِ سالمِ ووکامرس همیشه این هدر را دارد؛ نبودش = پاسخِ نامعتبر (بلاک/کش/آشغال)
        raise ValueError("X-WP-Total header missing (پاسخِ نامعتبرِ ووکامرس)")
    return int(total)


async def total_count(endpoint, params=None):
    """شمارشِ کلِ یک منبع (products/orders/…) از هدرِ X-WP-Total."""
    return await _call(_count_sync, endpoint, params)


def _put_sync(endpoint, data):
    resp = _client().put(endpoint, data)
    resp.raise_for_status()
    return resp.json()


async def put(endpoint, data):
    return await _call(_put_sync, endpoint, data)


def _post_sync(endpoint, data):
    resp = _client().post(endpoint, data)
    resp.raise_for_status()
    return resp.json()


async def post(endpoint, data):
    return await _call(_post_sync, endpoint, data)


async def get_order(order_id: int):
    return await get(f"orders/{order_id}")


_product_cache = {}  # product_id -> (data, ts) — کشِ کوتاهِ محصول (تا موجودی خیلی کهنه نشود)
_PRODUCT_TTL = int(getattr(config, "WC_PRODUCT_TTL_S", 300))  # ۵ دقیقه


async def get_product(product_id: int):
    now = _time.time()
    hit = _product_cache.get(product_id)
    if hit and now - hit[1] < _PRODUCT_TTL:
        return hit[0]
    data = await get(f"products/{product_id}")
    if len(_product_cache) > 500:
        _product_cache.clear()
    _product_cache[product_id] = (data, now)
    return data


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
    global _req_count
    _req_count += 1
    resp = _client().get(endpoint, params=params or {})
    resp.raise_for_status()
    total = int(resp.headers.get("X-WP-TotalPages") or 1)
    return resp.json(), total


# فیلدهای سبک برای تشخیصِ تغییر (بدونِ detail): id/وضعیت/تاریخ‌ها
_MODIFIED_FIELDS = "id,number,status,date_created,date_modified_gmt"


async def list_modified_orders(modified_after_iso, statuses=None, max_pages=50):
    """سفارش‌های تغییرکرده پس از زمانِ داده‌شده (GMT). pagination اصولی. خروجی: (orders, pages)."""
    base = {
        "modified_after": modified_after_iso, "dates_are_gmt": "true",
        "per_page": 100, "orderby": "modified", "order": "asc", "_fields": _MODIFIED_FIELDS,
    }
    if statuses:
        base["status"] = ",".join(statuses)
    out, page, pages = [], 1, 0
    while page <= max_pages:
        batch, total = await _call(_get_paged_sync, "orders", {**base, "page": page})
        pages += 1
        if not batch:
            break
        out.extend(batch)
        if page >= max(1, total):
            break
        page += 1
        await asyncio.sleep(0.25)  # delay کوتاه بین صفحه‌ها (فشارِ کمتر روی سایت)
    return out, pages


async def list_orders_in_range(after_iso, before_iso):
    """همه‌ی سفارش‌های یک بازه؛ صفحه‌ی اول برای تعداد صفحات، بقیه به‌صورت موازی."""
    base = {
        "after": after_iso, "before": before_iso, "per_page": 100,
        "orderby": "date", "order": "asc", "_fields": _RANGE_FIELDS,
    }
    first, total_pages = await _call(_get_paged_sync, "orders", {**base, "page": 1})
    out = list(first)
    if total_pages > 1:
        rest = await asyncio.gather(*[
            _call(_get_sync, "orders", {**base, "page": p})
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

    # تفکیکِ مالی: جمعِ قبل از تخفیفِ اقلام (subtotalِ هر آیتم قبل از تخفیف است)
    items_subtotal = sum(float(li.get("subtotal") or 0) for li in (order.get("line_items") or []))
    coupons = [str(c.get("code", "")).strip() for c in (order.get("coupon_lines") or []) if c.get("code")]

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
        "discount_total": order.get("discount_total", "") or "0",
        "shipping_total": order.get("shipping_total", "") or "0",
        "items_subtotal": items_subtotal,
        "coupons": coupons,
        "date_created": order.get("date_created"),
    }
