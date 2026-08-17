"""ttbol.py — استخراجِ محصولاتِ برند از competitor-shop.example (WooCommerce Store API، عمومی، بدونِ احراز).

Store API اتریبیوت‌ها را ساختارمند می‌دهد (بدونِ HTML). فیلترِ برند با term_idِ pa_brand.
رفرنس در نامِ محصول است (آخرین توکنِ مدل‌مانند). خروجی برای مَپر/دفترچه + دکمهٔ /brand.
"""
from __future__ import annotations

import re
import time

import requests

BASE = "https://competitor-shop.example"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
       "Accept": "application/json"}
_BRAND_ATTR = 7  # pa_brand
# توکنِ مدل‌مانند: حرف/عدد + شاملِ رقم + شاملِ . یا - (مثل LC08348.351، MTP-B190L-7BVDF، HNG1033.567)
_REF_TOK = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9.\-]{3,}$")


def _get(path: str, params: dict | None = None, tries: int = 3):
    last = None
    for _ in range(tries):
        try:
            r = requests.get(f"{BASE}/wp-json/wc/store/v1/{path}", params=params or {}, headers=_UA, timeout=30)
            r.raise_for_status()
            return r.json(), int(r.headers.get("X-WP-Total") or 0)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise last


def brands() -> dict:
    """{نامِ برند: term_id}."""
    d, _ = _get(f"products/attributes/{_BRAND_ATTR}/terms", {"per_page": 100, "orderby": "count", "order": "desc"})
    return {t["name"]: t["id"] for t in d}


def extract_ref(name: str) -> str:
    toks = [t.strip(".,") for t in (name or "").split()]
    cands = [t for t in toks if _REF_TOK.match(t)]
    return cands[-1].upper() if cands else ""


def _attrs(p: dict) -> dict:
    out = {}
    for a in p.get("attributes") or []:
        out[a.get("name", "")] = [t.get("name", "") for t in (a.get("terms") or [])]
    return out


def list_brand(term_id: int, max_pages: int = 120, sleep: float = 0.3) -> list[dict]:
    """همهٔ محصولاتِ یک برند با اتریبیوتِ کامل."""
    out, page = [], 1
    while page <= max_pages:
        params = {"per_page": 100, "page": page,
                  "attributes[0][attribute]": "pa_brand", "attributes[0][term_id]": term_id}
        d, _total = _get("products", params)
        if not d:
            break
        for p in d:
            imgs = p.get("images") or []
            out.append({
                "id": p.get("id"), "name": p.get("name", ""), "slug": p.get("slug", ""),
                "sku": p.get("sku"), "ref": extract_ref(p.get("name", "")),
                "price": (p.get("prices") or {}).get("price"),
                "in_stock": p.get("is_in_stock", True),
                "image": (imgs[0].get("src") if imgs else None),
                "attrs": _attrs(p),
                "url": p.get("permalink", ""),
            })
        if len(d) < 100:
            break
        page += 1
        time.sleep(sleep)
    return out
