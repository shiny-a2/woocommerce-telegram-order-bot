"""ttbol_extract_job.py — استخراجِ برند از competitor-shop.example برای دکمهٔ چندسایتهٔ /brand.

برندِ فارسی → برندِ ttbol (Store API) → فقط محصولاتی که روی سایت «نداریم» (دیدوپ با رفرنس)
→ ttbol_map → اکسلِ عکس‌دار (بسته‌های ۱۰۰تایی) → ارسال به اپراتور + مالک. هم‌ساختار با irantimer_extract_job.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import ttbol                       # noqa: E402
import ttbol_map                   # noqa: E402
import woo                         # noqa: E402
import irantimer_extract_job as J  # noqa: E402  (بازاستفاده: _tg, _recipients, _build_excel)

# برندِ فارسی → (کلیدِ نامِ ttbol برای یافتنِ term، ترمِ برندِ سایتِ ما برای دیدوپ)
BRANDS = {
    "سیتیزن": ("CITIZEN", 3197), "لی کوپر": ("COOPER", 5458), "لی‌کوپر": ("COOPER", 5458),
    "کاترپیلار": ("Caterpillar", 5422), "دنیل کلین": ("DANIEL KLEIN", 4695), "دنیل‌کلین": ("DANIEL KLEIN", 4695),
    "سواچ": ("SWATCH", 1833), "اورینت": ("ORIENT", 6519), "آیس واچ": ("ICE-WATCH", 25259),
    "فسیل": ("FOSSIL", 4812), "سانتاباربارا": ("SANTA BARBARA", 5110), "پولو سانتا باربارا": ("SANTA BARBARA", 5110),
    "کاسیو": ("CASIO", None), "سیکو": ("SEIKO", None), "تایمکس": ("TIMEX", None),
}


def _site_term_dynamic(name):
    try:
        terms = woo._get_sync("products/attributes/103/terms", {"per_page": 100, "search": name, "_fields": "id,name"})
        for t in terms:
            if J._norm(t.get("name")) == J._norm(name):
                return t["id"]
        return terms[0]["id"] if terms else None
    except Exception:  # noqa: BLE001
        return None


def resolve_brand(name):
    name = J._norm(name)
    ent = None
    for k, v in BRANDS.items():
        if name == k or name.replace(" ", "") == k.replace(" ", ""):
            ent = (k, v)
            break
    if not ent:
        return None, None, None, None
    canon, (tb_key, site_term) = ent
    if site_term is None:
        site_term = _site_term_dynamic(canon)
    br = ttbol.brands()
    tb_term = next((v for kk, v in br.items() if tb_key.upper() in kk.upper()), None)
    return tb_term, site_term, canon, tb_key


def run(brand_name, offset=0, batch=None):
    tb_term, site_term, canon, _key = resolve_brand(brand_name)
    if not tb_term:
        for oid in J._recipients():
            J._tg("sendMessage", {"chat_id": str(oid),
                  "text": f"❌ برندِ «{brand_name}» در ttbol نگاشت نشده. موجود: {'، '.join(sorted(BRANDS))}"})
        return 1
    for oid in J._recipients():
        J._tg("sendMessage", {"chat_id": str(oid),
              "text": f"⏳ استخراجِ کاملِ «{canon}» از competitor-shop.example… (کل برند، ممکن است چند دقیقه طول بکشد)"})
    prods = ttbol.list_brand(tb_term)
    have = J.site_refs_for(site_term)
    new = [p for p in prods if p.get("ref") and p["ref"].upper().replace(" ", "") not in have]
    total_new = len(new)
    # پیش‌فرض: کلِ برند در یک فایل (batch=None). offset فقط برای ادامهٔ دستی.
    chunk = new[offset:] if batch is None else new[offset:offset + batch]
    rows = [(ttbol_map.map_product(p, canon),
             {"title": p["name"], "price_toman": p["price"], "in_stock": p["in_stock"],
              "image": p["image"], "url": p["url"]}) for p in chunk]
    ts = os.path.join(_HERE, "data", f"ttbol-{canon}-{offset}-{offset + len(chunk)}.xlsx")
    imgs = J._build_excel(rows, canon, ts)
    with open(ts, "rb") as f:
        data = f.read()
    if batch is None:
        span, more = f"کلِ برند: {len(rows)} محصول", "\n\n✅ کاملِ این برند استخراج شد."
    else:
        nxt = offset + batch
        span = f"این بسته: {offset + 1}–{offset + len(chunk)} ({len(rows)} محصول)"
        more = f"\n\nبرای بستهٔ بعدی از {nxt} دوباره بزن." if nxt < total_new else "\n\nآخرین بسته بود."
    cap = (f"📘 competitor-shop.example — «{canon}» (محصولاتی که روی سایت نداریم)\n\n"
           f"• کلِ جدید: {total_new}\n• {span}، {imgs} عکس\n\n"
           f"قوانینِ ttbol اعمال شده. با عکس تطبیق بده.{more}")
    for oid in J._recipients():
        J._tg("sendDocument", {"chat_id": str(oid), "caption": cap},
              {"document": (os.path.basename(ts), data,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    return 0


if __name__ == "__main__":
    brand = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TB_BRAND", "")
    off = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("TB_OFFSET", "0"))
    sys.exit(run(brand, offset=off))
