"""wt_pricesync.py — همگام‌سازیِ قیمت/موجودیِ ووکامرس از فایلِ JSONِ کانالِ تأمین‌کننده (کاترپیلار/فیلیپ‌پلین).

قواعد (تأییدشدهٔ مالک):
- فقط برندهای allowlist (کاترپیلار، فیلیپ‌پلین). بقیه لمس نمی‌شوند.
- رفرنسِ کاملاً مچ (اتریبیوتِ «رفرانس»، فاصله→نقطه): قیمت = عددِ کانال (مستقیم، هم‌واحد با سایت).
    · بی‌تعداد (manage_stock=false) → stock_status=instock (بدونِ روشن‌کردنِ ردیابیِ مقدار).
    · تعدادی (qty≥1) → موجودی دست‌نخورده؛ فقط قیمت.
- بی‌تعداد و نبود در کانال → stock_status=outofstock.
- تعدادی (qty≥1) و نبود در کانال → موجودی دست‌نخورده؛ قیمت از هم‌خانواده (کاترپیلار: ۲ بخشِ اول). چند قیمتیِ مبهم → گزارش، بدونِ تغییر.
- رفرنسِ کانال بدونِ محصولِ سایت → گزارش (محصول ساخته نمی‌شود).

ایمنی: dry-run پیش‌فرض؛ نوشتن فقط با apply=True و woo.put (به‌روزرسانیِ جزئی، فقط فیلدِ لازم). idempotent (بی‌تغییر → نوشتن نمی‌شود).
"""
from __future__ import annotations

import json
import os
import re

# فایلِ اسنپ‌شاتِ کانال که سرویسِ tg-outreach (با سشنِ زندهٔ مجموعه) اتمیک می‌نویسد.
CHANNEL_FILE = os.environ.get("WT_PRICESYNC_CHANNEL_FILE", r"C:\A2\tg-outreach\data\catgroup_prices.json")

BRANDS = {"کاترپیلار", "فیلیپ پلین"}     # allowlistِ برند (نام برند). فقط این‌ها لمس می‌شوند.
REF_ATTR = "رفرانس"
BRAND_ATTR = "نام برند"
_FA_NUM = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MIN_PRICE = 1_000_000                    # قیمتِ واقعی حداقل ۷ رقم (ریال) — کمتر = نادیده
_MAX_PRICE = 10_000_000_000               # سقفِ منطقی (ریال). بالاتر = گروهِ اولِ اشتباهاً چسبیده


def parse_price(text) -> int | None:
    """«189.900.000» یا «199/900/000» یا «۲۷۹٬۹۰۰٬۰۰۰» → 189900000. کوچک/بی‌معنا → None."""
    t = str(text or "").translate(_FA_NUM)
    m = re.search(r"(\d[\d./٬,\s]*\d)\s*$", t.strip())     # گروهِ عددیِ انتهایی
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    if len(digits) < 6:
        return None
    val = int(digits)
    return val if val >= _MIN_PRICE else None


def normalize_ref(text) -> str:
    """رفرنس را یکدست می‌کند: فاصله(ها)→نقطه، بزرگ، بدونِ نقطهٔ تکراری/کناری. «AD 149 11  132»→«AD.149.11.132»."""
    t = str(text or "").strip().upper()
    t = re.sub(r"\s+", ".", t)
    t = re.sub(r"\.+", ".", t).strip(".")
    return t


def parse_caption(text) -> tuple[str, int] | None:
    """کپشنِ «<رفرنس>   <قیمت>» → (ref_normalized, price). اگر قیمت/رفرنس نبود → None."""
    t = str(text or "").translate(_FA_NUM).strip()
    if not t:
        return None
    # قیمت = گروهِ عددیِ انتهایی. جداکننده‌ها: . / , ٬ . گروهِ اول تا ۴ رقم («1099.900.000» بدونِ جداکنندهٔ میلیارد).
    m = re.search(r"((?:\d{1,4}[ ])?\d{1,4}(?:[./,٬]\d{3})+|\d{6,})[ ]*$", t)
    if not m:
        return None
    price = int(re.sub(r"\D", "", m.group(1)))
    if price > _MAX_PRICE:                     # گروهِ اول اشتباهاً چسبیده؟ بدونِ آن دوباره
        m2 = re.search(r"(\d{1,4}(?:[./,٬]\d{3})+|\d{6,})[ ]*$", t)
        if m2:
            p2 = int(re.sub(r"\D", "", m2.group(1)))
            if _MIN_PRICE <= p2 <= _MAX_PRICE:
                m, price = m2, p2
    if price < _MIN_PRICE or price > _MAX_PRICE:
        return None
    ref = normalize_ref(t[:m.start()])
    if not ref or not re.search(r"[A-Z0-9]", ref):
        return None
    return ref, price


def parse_channel_json(data) -> dict:
    """خروجیِ export تلگرام → {ref: price}. فقط پیام‌هایی با کپشنِ رفرنس+قیمت. آخرین مقدار برای هر رفرنس."""
    out, stats = {}, {"messages": 0, "parsed": 0, "skipped": 0}
    msgs = data.get("messages") if isinstance(data, dict) else data
    for m in (msgs or []):
        if not isinstance(m, dict) or m.get("type") != "message":
            continue
        stats["messages"] += 1
        text = m.get("text")
        if isinstance(text, list):     # text_entities به‌صورتِ لیست → به رشته تبدیل
            text = "".join(x if isinstance(x, str) else x.get("text", "") for x in text)
        pr = parse_caption(text)
        if pr:
            out[pr[0]] = pr[1]         # آخرین (ویرایش‌شده) برنده
            stats["parsed"] += 1
        else:
            stats["skipped"] += 1
    return out, stats


def family_key(ref) -> str | None:
    """خانواده = بخشِ اولِ رفرنس (حروفِ ابتدایی تا اولین نقطه). AK.199.21.629 → AK ؛ AD.149.11.132 → AD.

    قاعدهٔ مالک: اگر کلِ خطِ خانواده در کانال تک‌قیمت باشد، همهٔ محصولاتِ آن خانواده به همان قیمت بروز می‌شوند؛
    اگر چندقیمت باشد (مثلِ AD که AD.143≠AD.149) خانواده «مبهم» است و بدونِ تغییر در اکسل گزارش می‌شود.
    """
    if not ref:
        return None
    return ref.split(".")[0] or None


def product_view(p) -> dict:
    """از محصولِ خامِ ووکامرس، فیلدهای لازم را درمی‌آورد (attribute «نام برند» و «رفرانس»)."""
    brand = ref = None
    for a in (p.get("attributes") or []):
        nm = (a.get("name") or "").strip()
        opts = a.get("options") or []
        if nm == BRAND_ATTR and opts:
            brand = opts[0]
        elif nm == REF_ATTR and opts:
            ref = opts[0]
    try:
        rp = int(re.sub(r"\D", "", str(p.get("regular_price") or "")) or 0)
    except ValueError:
        rp = 0
    return {"id": p.get("id"), "name": p.get("name"), "brand": brand,
            "ref": normalize_ref(ref) if ref else "", "manage_stock": bool(p.get("manage_stock")),
            "stock_status": p.get("stock_status"), "stock_quantity": p.get("stock_quantity"),
            "regular_price": rp}


def plan_changes(channel: dict, products: list) -> dict:
    """محاسبهٔ خالصِ تغییرات (بدونِ I/O). products = خروجیِ product_view روی محصولاتِ برندهای مجاز."""
    fam: dict = {}
    for ref, price in channel.items():
        fk = family_key(ref)
        if fk:
            fam.setdefault(fk, set()).add(price)
    out = {"price_exact": [], "price_family": [], "set_instock": [], "set_outofstock": [],
           "untouched_qty": 0, "ambiguous_family": [], "unmatched_refs": [], "no_ref": 0,
           "qty_review": []}   # محصولاتِ تعدادی که قیمتشان بروز نشد (خانواده در کانال نیست/مبهم)
    matched = set()
    for p in products:
        if p["brand"] not in BRANDS:            # ایمنی: فقط برندهای مجاز
            continue
        ref = p["ref"]
        if not ref:
            out["no_ref"] += 1
            continue
        qty_tracked = p["manage_stock"] and (p["stock_quantity"] or 0) >= 1
        if qty_tracked:                          # موجودیِ تعدادی هرگز لمس نمی‌شود (فقط قیمت)
            out["untouched_qty"] += 1
        exact = ref in channel
        fk = family_key(ref)
        famprices = None if exact else fam.get(fk)
        # ---------- قیمت (روی همه: تعدادی + غیرتعدادی) ----------
        if exact:
            matched.add(ref)
            target = channel[ref]
            if p["regular_price"] != target:
                out["price_exact"].append({"ref": ref, "id": p["id"], "old": p["regular_price"], "new": target})
        elif famprices and len(famprices) == 1:          # خانوادهٔ تک‌قیمت → قیمتِ خانواده روی همه
            fp = next(iter(famprices))
            if p["regular_price"] != fp:
                out["price_family"].append({"ref": ref, "id": p["id"], "old": p["regular_price"],
                                            "new": fp, "family": fk})
        elif famprices and len(famprices) > 1:           # خانوادهٔ چندقیمت → مبهم، بدونِ تغییرِ قیمت
            out["ambiguous_family"].append({"ref": ref, "id": p["id"], "family": fk, "prices": sorted(famprices)})
            if qty_tracked:
                out["qty_review"].append({"ref": ref, "id": p["id"], "name": p.get("name") or "",
                                          "price": p["regular_price"], "family": fk, "reason": "چند قیمتِ مبهم در خانواده"})
        else:                                            # خانواده در کانال نیست → قیمتِ کانالی ندارد
            if qty_tracked:
                out["qty_review"].append({"ref": ref, "id": p["id"], "name": p.get("name") or "",
                                          "price": p["regular_price"], "family": fk or "—", "reason": "خانواده در کانال نیست"})
        # ---------- موجودی (فقط غیرتعدادی؛ تعدادی هرگز لمس نمی‌شود) ----------
        if not qty_tracked:
            if exact:                                    # فقط رفرنسِ عیناً دقیق → موجود
                if p["stock_status"] != "instock":
                    out["set_instock"].append({"ref": ref, "id": p["id"]})
            else:                                        # خانواده‌ای/نامطابق → موجود نکن؛ ناموجود
                if p["stock_status"] != "outofstock":
                    out["set_outofstock"].append({"ref": ref, "id": p["id"]})
    out["unmatched_refs"] = sorted(r for r in channel if r not in matched)
    return out


def load_channel_file(path=None) -> tuple[dict, dict]:
    """catgroup_prices.json (ساختهٔ tg-outreach) → (prices:{ref:int}, meta). فایلِ نبود/خراب → استثنا."""
    p = path or CHANNEL_FILE
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    prices = {str(k): int(v) for k, v in (d.get("prices") or {}).items()}
    meta = {"channel": d.get("channel"), "updated_ts": d.get("updated_ts"), "count": d.get("count"),
            "messages": d.get("messages"), "parsed": d.get("parsed"), "sample": d.get("sample") or []}
    return prices, meta


def summarize(plan: dict) -> str:
    return (f"قیمتِ رفرنسِ‌دقیق: {len(plan['price_exact'])} · قیمتِ هم‌خانواده: {len(plan['price_family'])} · "
            f"→موجود: {len(plan['set_instock'])} · →ناموجود: {len(plan['set_outofstock'])} · "
            f"موجودیِ تعدادیِ دست‌نخورده: {plan['untouched_qty']} · مبهمِ هم‌خانواده: {len(plan['ambiguous_family'])} · "
            f"تعدادیِ نیازمندِ بررسی: {len(plan.get('qty_review', []))} · "
            f"رفرنسِ بی‌محصول: {len(plan['unmatched_refs'])} · بی‌رفرنس: {plan['no_ref']}")


# ---------- آداپترِ ووکامرس (read برای plan؛ write فقط در apply) ----------
async def fetch_brand_products(woo, per_page=100, max_pages=60) -> list:
    """همهٔ محصولاتِ برندهای مجاز را (paginated) می‌خواند. خروجی = list[product_view]. فقط read."""
    seen, out = set(), []
    fields = "id,name,regular_price,manage_stock,stock_status,stock_quantity,attributes"
    for page in range(1, max_pages + 1):
        # جست‌وجوی نامِ برند (fuzzy) سپس فیلترِ دقیق روی attribute در کد
        batch = await woo.get("products", {"search": "کاترپیلار", "per_page": per_page, "page": page, "_fields": fields})
        if not batch:
            break
        for p in batch:
            if p.get("id") in seen:
                continue
            seen.add(p["id"])
            pv = product_view(p)
            if pv["brand"] in BRANDS:
                out.append(pv)
        if len(batch) < per_page:
            break
    for page in range(1, max_pages + 1):
        batch = await woo.get("products", {"search": "فیلیپ پلین", "per_page": per_page, "page": page, "_fields": fields})
        if not batch:
            break
        for p in batch:
            if p.get("id") in seen:
                continue
            seen.add(p["id"])
            pv = product_view(p)
            if pv["brand"] in BRANDS:
                out.append(pv)
        if len(batch) < per_page:
            break
    return out


async def apply_plan(woo, plan: dict, limit=None) -> dict:
    """نوشتنِ واقعی روی سایت (فقط فیلدِ لازم، به‌روزرسانیِ جزئی). فقط وقتی کالر صریح می‌خواهد."""
    res = {"price": 0, "instock": 0, "outofstock": 0, "errors": []}
    n = 0

    async def _put(pid, payload, kind):
        nonlocal n
        try:
            await woo.put(f"products/{pid}", payload)
            res[kind] += 1
        except Exception as e:  # noqa: BLE001
            res["errors"].append({"id": pid, "kind": kind, "err": type(e).__name__})
        n += 1

    for c in plan["price_exact"] + plan["price_family"]:
        if limit and n >= limit:
            return res
        await _put(c["id"], {"regular_price": str(c["new"])}, "price")
    for c in plan["set_instock"]:
        if limit and n >= limit:
            return res
        # manage_stock=False لازم است: اگر ردیابیِ مقدار روشن باشد و مقدار خالی/صفر، ووکامرس
        # وضعیت را از مقدار می‌گیرد و «instock» را به outofstock برمی‌گرداند. طبقِ قاعدهٔ مالک،
        # محصولِ «بدونِ تعداد» نباید ردیابیِ مقدار داشته باشد → خاموشش می‌کنیم تا instock بچسبد.
        await _put(c["id"], {"stock_status": "instock", "manage_stock": False}, "instock")
    for c in plan["set_outofstock"]:
        if limit and n >= limit:
            return res
        await _put(c["id"], {"stock_status": "outofstock"}, "outofstock")
    return res
