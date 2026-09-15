"""irantimer.py — استخراجِ دقیقِ محصولاتِ برند از competitor-catalog.example (پلتفرمِ Simia/ASP.NET).

طراحی: صفحهٔ لیست فقط «ایندکسِ IDها»ست؛ صفحهٔ محصول منبعِ حقیقت است
(رفرنس + قیمت + موجودی + جدولِ مشخصات). پارسِ ساختاری و قطعی — نه حدسِ AI.

نکاتِ مارک‌آپِ Simia که پارسر باید رعایت کند:
  • جدولِ مشخصات = چند <div class="PRtc1Section"><table>… با ردیف‌های <td>برچسب</td><td>مقدار</td>.
  • هر گروه یک ردیفِ <th colspan="2">نامِ‌گروه</th> دارد (تک‌سلولی → در پارسِ دوسلولی نادیده می‌ماند).
  • داخلِ سلولِ مقدار، <span class="TooltipIcon"><span class="TooltipBox">…</span></span> = متنِ توضیح/تکراری
    و باید کاملاً حذف شود (منشأِ نویزِ «خاکستریخاکستری سبز»).
  • چند مقدار با فاصله‌های زیاد/‎&nbsp;‎ جدا می‌شوند → split.
"""
from __future__ import annotations

import html
import json
import os
import re
import time

import requests

BASE = "https://www.competitor-catalog.example"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_REF_RE = re.compile(r"[A-Z]{2}\d{3,4}[A-Z]?-\d{2}[A-Z]")  # رفرنسِ سبکِ سیتیزن/ساعت

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _en(s: str) -> str:
    return (s or "").translate(_FA)


def _get(url: str, tries: int = 3) -> str:
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, headers=_UA, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise last


_BRAND_LINK_RE = re.compile(
    r'<a[^>]+href="[^"]*?/Fa-Products-1-0-0-(\d+)/([a-z0-9\-]+)"[^>]*>(.*?)</a>', re.S)
_ALT_RE = re.compile(r'alt="([^"]*)"')
BRANDS_PAGE = "/ProductsBrand.aspx?Group=1"


def list_brands() -> list[dict]:
    """کاتالوگِ کاملِ برندهای irantimer از صفحهٔ رسمیِ برندها → [{id, slug, name}].

    ⚠️ نامِ فارسی در `alt`ِ تصویرِ داخلِ لینک است، نه در متنِ لینک (لینک فقط یک <img> دارد).
    نسخهٔ اولِ این استخراج متنِ لینک را می‌خواند و چون خالی بود به متنِ منوی اطراف می‌افتاد —
    برای همین نام‌هایی مثلِ «لیست محصولات» و «بر اساس ویژگی‌ها» در کاتالوگِ قدیمی نشسته بود.
    """
    h = _get(BASE + BRANDS_PAGE)
    out: dict[str, dict] = {}
    for bid, slug, inner in _BRAND_LINK_RE.findall(h):
        m = _ALT_RE.search(inner)
        name = html.unescape(m.group(1)).strip() if m else ""
        if not name:
            name = _strip(inner)
        # اولین دیدنِ هر id برنده است؛ اگر نامِ بعدی بهتر بود (قبلی خالی) جایگزین می‌شود
        if bid not in out or (not out[bid]["name"] and name):
            out[bid] = {"id": int(bid), "slug": slug, "name": name}
    return sorted(out.values(), key=lambda b: b["name"] or b["slug"])


_DETAIL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "irantimer_details")


def parse_detail_cached(pid: str) -> dict:
    """مثلِ parse_detail ولی روی دیسک کش می‌شود.

    صفحهٔ جزئیات گرانِ‌ترین بخشِ کار است (یک درخواست برای هر محصول). با کش، اجرای دوبارهٔ یک برند
    تقریباً رایگان است — مهم است چون استخراجِ برندهای بزرگ ساعت‌ها طول می‌کشد و اگر وسطش قطع شود
    نباید همه‌چیز از نو شروع شود. مشخصاتِ فنی عوض نمی‌شوند؛ قیمت/موجودی از این مسیر مصرف نمی‌شود.
    """
    p = os.path.join(_DETAIL_CACHE, f"{pid}.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 — کشِ خراب = انگار نبوده
            pass
    d = parse_detail(str(pid))
    try:
        os.makedirs(_DETAIL_CACHE, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 — نشدنِ کش نباید کار را بشکند
        pass
    return d


_H1_RE = re.compile(r'<h1 property="name">(.*?)</h1>', re.S)
_TOTAL_RE = re.compile(r'<span class="TotalItemFound">\s*(\d+)\s*</span>')
_LEAD_RE = re.compile(r"^\s*ساعت\s+(?:مچی\s+)?")


def brand_info(brand_id: int, group: int = 1) -> dict:
    """یک fetch از صفحهٔ لیستِ برند → {id, name, title, total}.

    این مطمئن‌ترین منبعِ نامِ فارسی است: <h1 property="name">ساعت مچی سیکو SEIKO</h1>.
    «TotalItemFound» هم تعدادِ کلِ محصولاتِ آن برند را می‌دهد، بدونِ ورق‌زدنِ صفحات.
    """
    url = f"{BASE}/ProductsList.aspx?Luxury=0&Brand={brand_id}&Group={group}&page=1"
    h = _get(url)
    m = _H1_RE.search(h)
    title = _strip(m.group(1)) if m else ""
    # «ساعت مچی سیکو SEIKO» → «سیکو»: پیشوندِ ثابت و دنبالهٔ لاتین را برمی‌داریم
    name = _LEAD_RE.sub("", title)
    name = re.sub(r"\s*[A-Za-z][A-Za-z0-9 .&'\-]*$", "", name).strip()
    t = _TOTAL_RE.search(h)
    return {"id": int(brand_id), "name": name, "title": title,
            "total": int(t.group(1)) if t else 0}


def _strip(s: str) -> str:
    """متنِ تمیزِ تک‌خطی از HTML (تولتیپ حذف)."""
    s = re.sub(r'<span class="TooltipIcon">.*?</span>', "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _values(cell_html: str) -> list[str]:
    """مقادیرِ چندگانهٔ یک سلول (تولتیپ حذف، split روی مرزِ تگ/فاصلهٔ زیاد)."""
    s = re.sub(r'<span class="TooltipIcon">.*?</span>', "", cell_html, flags=re.S)
    s = re.sub(r"<[^>]+>", "\x00", s)  # مرزِ تگ‌ها
    s = html.unescape(s).replace("\xa0", " ")
    parts = re.split(r"[\x00]|\s{2,}", s)
    out, seen = [], set()
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ---------- لیستِ برند (ایندکسِ id + رفرنس) ----------
_PLITEM_RE = re.compile(r'class="PLitem"(.*?)(?=class="PLitem"|<div class="PLfiltersButton"|$)', re.S)


def _parse_card(block: str) -> dict | None:
    mid = re.search(r"Fa-Product-(\d+)", block) or re.search(r'PLinfo">(\d+)<', block)
    if not mid:
        return None
    pid = mid.group(1)
    # رفرنس داخلِ عنوانِ کارت: «… مدل FE1241-71L»
    mref = re.search(r"مدل\s*([A-Z0-9][A-Z0-9.\-]{4,})", block) or _REF_RE.search(block)
    ref = None
    if mref:
        ref = (mref.group(1) if mref.re is not _REF_RE else mref.group(0)).upper().strip()
    oos = bool(re.search(r'IT2-OutOfStock"(?![^>]*display:\s*none)', block))
    return {"id": pid, "ref": ref, "in_stock": not oos}


def list_products(brand: int, group: int = 1, max_pages: int = 120, sleep: float = 0.2) -> list[dict]:
    """همهٔ محصولاتِ یک برند/گروه به‌صورت [{id, ref, in_stock}] — فقط از صفحاتِ لیست (سریع)."""
    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    while page <= max_pages:
        url = f"{BASE}/ProductsList.aspx?Luxury=0&Brand={brand}&Group={group}&page={page}"
        h = _get(url)
        cards = [_parse_card(b) for b in _PLITEM_RE.findall(h)]
        cards = [c for c in cards if c]
        new = [c for c in cards if c["id"] not in seen]
        if not new:
            break
        for c in new:
            seen.add(c["id"])
            out.append(c)
        if len(cards) < 20:
            break
        page += 1
        time.sleep(sleep)
    return out


def list_product_ids(brand: int, group: int = 1, max_pages: int = 120) -> list[str]:
    return [c["id"] for c in list_products(brand, group, max_pages)]


# ---------- صفحهٔ محصول (منبعِ حقیقت) ----------
def parse_detail_html(pid: str, h: str) -> dict:
    specs: dict[str, str] = {}
    for sec in re.findall(r'<div class="PRtc1Section">(.*?)</div>', h, re.S):
        for row in re.finditer(r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", sec, re.S):
            label = _strip(row.group(1))
            vals = _values(row.group(2))
            if label and vals:
                specs[label] = " / ".join(vals)
    ref = None
    mref = _REF_RE.search(h)
    if mref:
        ref = mref.group(0)
    # قیمت (تومانِ irantimer — فقط مرجع؛ قیمتِ فروشِ ما را مالک/فرمول تعیین می‌کند)
    prices = []
    for x in re.findall(r'([\d,۰-۹٠-٩]{6,})\s*(?:تومان|ریال)', h):
        try:
            prices.append(int(_en(x).replace(",", "")))
        except ValueError:
            pass
    price = max(prices) if prices else None
    tm = re.search(r"<title>(.*?)</title>", h, re.S)
    title = _strip(tm.group(1)) if tm else None
    # موجودی: بلوکِ IT2-OutOfStock که display:none نباشد = ناموجود
    oos = bool(re.search(r'IT2-OutOfStock"(?![^>]*display:\s*none)', h))
    img = None
    mimg = re.search(r'(https://competitor-catalog.example/Images/Products/[^"]+\.jpg)', h)
    if mimg:
        img = mimg.group(1)
    return {"id": pid, "ref": ref, "title": title, "price_toman": price,
            "in_stock": not oos, "specs": specs, "image": img,
            "url": f"{BASE}/Fa-Product-{pid}/x"}


def parse_detail(pid: str) -> dict:
    return parse_detail_html(pid, _get(f"{BASE}/Fa-Product-{pid}/x"))
