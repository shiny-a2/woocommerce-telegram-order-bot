"""saati.py — کلاینتِ APIِ تأمین‌کنندهٔ «سیتیزن» (app.supplier.example → api.supplier.example).

لاگین با OTP: sendcode (پیامک) → verifycode (mobile+code) → توکنِ JWT که با هدرِ Authorization: Bearer فرستاده می‌شود.
محصولات: GET /agent/Product (sku, title, categoryId, price1/price2, count1/count2, image) — صفحه‌بندی با page.
پاکتِ پاسخ: {"data":..., "status":bool, "code":int, "error":str, "msg":str}. خطای احراز = HTTP 401.

توکن در db.meta('saati_token') ذخیره می‌شود (بادوام). هیچ سکرتی لاگ نمی‌شود.
"""
from __future__ import annotations

import base64
import json
import time

import requests

import config
import db

BASE = "https://api.supplier.example"
_TIMEOUT = (6, 25)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")   # فارسی/عربی → انگلیسی


def _en(s) -> str:
    """فقط ارقامِ انگلیسی (فارسی/عربی → انگلیسی، بقیه حذف). API فقط رقمِ ASCII می‌پذیرد."""
    return "".join(c for c in str(s or "").translate(_DIG) if c.isascii() and c.isdigit())


def mobile() -> str:
    return getattr(config, "WT_SAATI_MOBILE", "") or ""


def _headers(token: str | None = None) -> dict:
    h = {"User-Agent": _UA, "Accept": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _token() -> str:
    return db.get_meta("saati_token") or ""


def _json(r):
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


def token_exp() -> int:
    """انقضای JWT (epoch ثانیه) اگر خوانده شد، وگرنه 0."""
    t = _token()
    if not t or t.count(".") != 2:
        return 0
    try:
        p = t.split(".")[1]
        p += "=" * (-len(p) % 4)
        return int(json.loads(base64.urlsafe_b64decode(p)).get("exp") or 0)
    except Exception:  # noqa: BLE001
        return 0


def logged_in() -> bool:
    """توکن هست و (اگر انقضا خوانده شد) هنوز حداقل ۱ دقیقه اعتبار دارد."""
    if not _token():
        return False
    exp = token_exp()
    return exp == 0 or exp > time.time() + 60


def status() -> dict:
    exp = token_exp()
    return {"logged_in": logged_in(), "user": db.get_meta("saati_user") or "",
            "exp": exp, "exp_in_h": round((exp - time.time()) / 3600, 1) if exp else None}


def send_code(mob: str | None = None) -> dict:
    """درخواستِ کدِ پیامکی به موبایل. خروجی: {'ok':bool,'msg':str}."""
    try:
        r = requests.post(f"{BASE}/api/Auth/sendcode", params={"mobile": mob or mobile()},
                          headers=_headers(), timeout=_TIMEOUT)
        j = _json(r)
        return {"ok": bool(j.get("status")) and r.status_code == 200,
                "msg": (j.get("msg") or j.get("error") or "").strip(), "http": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": type(e).__name__}


def verify_code(code, mob: str | None = None) -> dict:
    """تأییدِ کد → ذخیرهٔ توکنِ JWT در db.meta. خروجی: {'ok':bool,'msg':str,'name':str}."""
    try:
        r = requests.post(f"{BASE}/api/Auth/verifycode",
                          params={"mobile": _en(mob or mobile()), "code": _en(code)},
                          headers=_headers(), timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": type(e).__name__}
    j = _json(r)
    if not j.get("status"):
        return {"ok": False, "msg": (j.get("msg") or j.get("error") or "کدِ نامعتبر").strip()}
    data = j.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        return {"ok": False, "msg": "توکن در پاسخِ سرور نبود"}
    name = (data.get("name") or data.get("userName") or "") if isinstance(data, dict) else ""
    db.set_meta("saati_token", token)
    db.set_meta("saati_user", name)
    return {"ok": True, "msg": "لاگین موفق", "name": name}


def get_products(page: int = 1, **filters) -> dict:
    """یک صفحه محصول. خروجی: {'ok':bool,'unauthorized':bool,'items':list,'raw':...}.

    unauthorized=True یعنی توکن منقضی/غایب → باید دوباره OTP گرفت.
    """
    if not _token():
        return {"ok": False, "unauthorized": True, "items": []}
    params = {"page": page}
    params.update({k: v for k, v in filters.items() if v is not None})
    try:
        r = requests.get(f"{BASE}/agent/Product", params=params, headers=_headers(_token()), timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": type(e).__name__, "items": []}
    if r.status_code == 401:
        return {"ok": False, "unauthorized": True, "items": []}
    j = _json(r)
    data = j.get("data") if isinstance(j, dict) else j
    if isinstance(data, dict):
        items = data.get("items") or data.get("list") or data.get("products") or []
    else:
        items = data if isinstance(data, list) else []
    return {"ok": bool(j.get("status", True)) and r.status_code == 200, "unauthorized": False,
            "items": items or [], "raw": j}


def fetch_all_products(max_pages: int = 400, **filters) -> dict:
    """همهٔ محصولات را صفحه‌به‌صفحه (ملایم). خروجی: {'ok','unauthorized','items':list,'pages':int}."""
    out, page = [], 1
    while page <= max_pages:
        pr = get_products(page, **filters)
        if pr.get("unauthorized"):
            return {"ok": False, "unauthorized": True, "items": out, "pages": page - 1}
        items = pr.get("items") or []
        if not items:
            break
        out.extend(items)
        if len(items) < 25:                 # صفحهٔ آخر (per_page پیش‌فرض ۲۵)
            break
        page += 1
        time.sleep(0.2)                      # فشار نیاور
    return {"ok": True, "unauthorized": False, "items": out, "pages": page}
