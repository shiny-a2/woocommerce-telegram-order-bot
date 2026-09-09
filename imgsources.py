"""imgsources.py — آداپتورهای مبدأِ عکس (مولتی‌سایت) برای imgpipe.

هر سایت یک آداپتور با قراردادِ یکسان:  discover(refs) → {ref: [url1, url2, ...]}  (اولی = عکسِ اصلی)
هیچ چیزی نمی‌نویسد؛ فقط کشف. بدونِ AI. شبکه فقط خواندنی (GET).

آداپتورهای فعلی
---------------
watchonline  — source-a.example (Next.js). آدرسِ محصول = /product/{ref}؛ یعنی lookupِ مستقیم با رفرنس،
               بدونِ کرالِ کل سایت. دادهٔ ساختاریافته از __NEXT_DATA__ → props.pageProps.product،
               شاملِ sku واقعی و gallery[].url (فول‌سایز). ⇒ مطمئن‌ترین مبدأ.
omax         — source-b.example (Shopify). /products.json?limit=250&page=N.
               ⚠️ SKUِ این سایت خراب است (۲۵۰ محصول ← ۱۰۳ SKU؛ یک SKU روی ۷۷ محصول تکرار شده)،
               پس رفرنس از «آخرِ عنوان» گرفته می‌شود — همان قاعده‌ای که mediaimg.product_ref دارد.
               اکثر محصولات فقط ۱ عکس دارند (۱۵۰۰×۱۸۰۰) که برای هیرو+گالریِ اسکریپتِ فوتوشاپ کافی است.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (compatible; product-image-sync/1.0)"}
_TITLE_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9.\-/]{3,})\s*$")   # همان قاعدهٔ mediaimg
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _get(url: str, timeout: int = 30, limit: int = 4_000_000) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()[:limit]
    except Exception:  # noqa: BLE001 — مبدأ ممکن است ۴۰۴/تایم‌اوت بدهد؛ عادی است
        return None


def norm_ref(s: str) -> str:
    """نرمال‌سازیِ رفرنس برای مقایسه (بدونِ فاصله/خط‌تیره، بی‌توجه به بزرگ‌وکوچک)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()


class SourceAdapter:
    name = "base"

    def discover(self, refs: list[str]) -> dict[str, list[str]]:
        raise NotImplementedError


class WatchOnlineAdapter(SourceAdapter):
    """source-a.example — lookupِ مستقیم با رفرنس (آدرس = /product/{ref})."""
    name = "watchonline"
    BASE = "https://www.source-a.example"

    def _product(self, ref: str) -> dict | None:
        raw = _get(f"{self.BASE}/product/{ref.strip().lower()}")
        if not raw:
            return None
        m = _NEXT_DATA.search(raw.decode("utf-8", "replace"))
        if not m:
            return None
        try:
            pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        except Exception:  # noqa: BLE001
            return None
        return pp.get("product") if isinstance(pp.get("product"), dict) else None

    def discover(self, refs: list[str], pause: float = 0.4) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for ref in refs:
            p = self._product(ref)
            if not p:
                continue
            # گاردِ هویتِ مثبت: یا sku دقیقاً برابرِ رفرنس باشد، یا رفرنس داخلِ عنوان بیاید.
            # صرفِ «۲۰۰ گرفتن» کافی نیست (ضدِ ۴۰۴ نرم / صفحهٔ fallback که عکسِ اشتباه بچسباند).
            nref = norm_ref(ref)
            sku_ok = bool(p.get("sku")) and norm_ref(p["sku"]) == nref
            title_ok = nref in norm_ref(p.get("title") or "")
            if not (sku_ok or title_ok):
                continue
            urls = []
            for g in (p.get("gallery") or []):
                u = g.get("url") if isinstance(g, dict) else None
                if u and u.startswith("http"):
                    urls.append(u)
            if urls:
                out[ref] = urls
            time.sleep(pause)
        return out


class OmaxShopifyAdapter(SourceAdapter):
    """source-b.example — Shopify. رفرنس از «آخرِ عنوان» (SKUِ سایت غیرقابل‌اعتماد است)."""
    name = "omax"
    BASE = "https://source-b.example"

    def _catalog(self, max_pages: int = 40, pause: float = 0.5) -> dict[str, list[str]]:
        """{norm_ref: [image urls]} از کلِ کاتالوگ (یک‌بار ساخته و کش می‌شود)."""
        index: dict[str, list[str]] = {}
        for page in range(1, max_pages + 1):
            raw = _get(f"{self.BASE}/products.json?limit=250&page={page}")
            if not raw:
                break
            try:
                prods = json.loads(raw).get("products", [])
            except Exception:  # noqa: BLE001
                break
            if not prods:
                break
            for p in prods:
                m = _TITLE_REF.search((p.get("title") or "").strip())
                if not m:
                    continue
                key = norm_ref(m.group(1))
                urls = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
                if key and urls and key not in index:      # اولین تطابق برنده (ضدِ رکوردهای copy)
                    index[key] = urls
            if len(prods) < 250:
                break
            time.sleep(pause)
        return index

    def discover(self, refs: list[str]) -> dict[str, list[str]]:
        idx = self._catalog()
        out = {}
        for ref in refs:
            urls = idx.get(norm_ref(ref))
            if urls:
                out[ref] = urls
        return out


REGISTRY: dict[str, type[SourceAdapter]] = {
    WatchOnlineAdapter.name: WatchOnlineAdapter,
    OmaxShopifyAdapter.name: OmaxShopifyAdapter,
}
DEFAULT_ORDER = ["watchonline", "omax"]   # اولویت: مبدأِ مطمئن‌تر (skuِ واقعی) اول


def discover_multi(refs: list[str], sites: list[str] | None = None,
                   max_images: int = 3) -> dict[str, dict]:
    """کشفِ مولتی‌سایت. برای هر رفرنس، سایت‌ها را به‌ترتیبِ اولویت امتحان می‌کند؛ اولین سایتی که
    عکس بدهد برنده است. خروجی: {ref: {"site": name, "urls": [...]}} — منبع برای گزارش ثبت می‌شود.
    """
    order = [s for s in (sites or DEFAULT_ORDER) if s in REGISTRY]
    found: dict[str, dict] = {}
    remaining = list(refs)
    for site in order:
        if not remaining:
            break
        try:
            got = REGISTRY[site]().discover(remaining)
        except Exception as e:  # noqa: BLE001 — یک مبدأِ خراب نباید کلِ کار را بشکند
            print(f"[imgsources] {site} خطا: {type(e).__name__}: {str(e)[:80]}")
            continue
        for ref, urls in got.items():
            if urls:
                found[ref] = {"site": site, "urls": urls[:max_images]}
        remaining = [r for r in remaining if r not in found]
    return found
