"""mediaimg.py — درجِ خودکارِ تصاویرِ محصولات از کتابخانهٔ رسانه بر اساسِ رفرنس.

منطق (طبقِ دستورِ مالک):
  • رفرنسِ هر محصول از اتریبیوتِ «رفرانس» (اگر نبود، از آخرِ عنوان) گرفته می‌شود.
  • عکسِ شاخص = فایلی که نامش دقیقاً «<ref>.<ext>» است.
  • گالری = «<ref>-1»، «<ref>-2»، … به ترتیبِ عدد (شاخص هرگز در گالری تکرار نمی‌شود).
  • اگر شاخص پیدا نشد → هیچ عکسی ست نمی‌شود و محصول در گزارشِ خطا ثبت می‌شود.
  • فقط images محصول ست می‌شود (wc/v3، query-string). قیمت/عنوان/ویژگی/… دست نمی‌خورد.
  • متادیتای رسانه (alt/title/caption) با فرآیندِ آپلودِ سایت از قبل پر است؛ اینجا نوشته نمی‌شود
    (کلیدِ ووکامرس اجازهٔ نوشتنِ wp/v2/media را ندارد — تأییدشده).
"""
from __future__ import annotations

import re

import requests

import config
import woo

_EXT = r"(?:webp|jpg|jpeg|png)"
_BASE = config.WOO_URL
_CK = config.WOO_CK
_CS = config.WOO_CS
# رفرنسِ داخلِ عنوان: آخرین توکنِ مدل‌مانند (حروف/عدد/نقطه/خط‌تیره)
_TITLE_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9.\-/]{3,})\s*$")


def product_ref(p: dict) -> str:
    for a in p.get("attributes", []):
        if a.get("name") == "رفرانس":
            opts = a.get("options") or []
            if opts and opts[0].strip():
                return opts[0].strip()
    m = _TITLE_REF.search((p.get("name") or "").strip())
    return m.group(1) if m else ""


def find_media(ref: str) -> tuple[int | None, list[int], list[str]]:
    """(featured_id, [gallery_ids by number], [matched filenames]) از کتابخانهٔ رسانه."""
    if not ref:
        return None, [], []
    r = requests.get(f"{_BASE}/wp-json/wp/v2/media",
                     params={"consumer_key": _CK, "consumer_secret": _CS, "search": ref, "per_page": 60},
                     timeout=30)
    r.raise_for_status()
    feat_pat = re.compile(rf"^{re.escape(ref)}\.{_EXT}$", re.I)
    gal_pat = re.compile(rf"^{re.escape(ref)}-(\d+)\.{_EXT}$", re.I)
    featured = None
    gallery: list[tuple[int, int]] = []
    files: list[str] = []
    for m in r.json():
        fn = (m.get("media_details", {}).get("file", "") or "").split("/")[-1]
        if feat_pat.match(fn):
            featured = m["id"]
            files.append(fn)
        else:
            g = gal_pat.match(fn)
            if g:
                gallery.append((int(g.group(1)), m["id"]))
                files.append(fn)
    gallery.sort(key=lambda x: x[0])
    return featured, [mid for _, mid in gallery], files


def fetch_noimage_products(brand_term: str | None = None, max_pages: int = 60) -> list[dict]:
    """محصولاتِ بدونِ عکس (اختیاراً محدود به یک برند). فقط id/name/attributes/images."""
    out = []
    for page in range(1, max_pages + 1):
        params = {"per_page": 100, "page": page, "orderby": "date", "order": "desc",
                  "_fields": "id,name,images,attributes,status"}
        if brand_term:
            params["attribute"] = "pa_نام-برند"
            params["attribute_term"] = brand_term
        ps = woo._get_sync("products", params)
        if not ps:
            break
        for p in ps:
            if not p.get("images"):
                out.append(p)
        if len(ps) < 100:
            break
    return out


def plan(products: list[dict]) -> list[dict]:
    rows = []
    for p in products:
        ref = product_ref(p)
        featured, gallery, files = (None, [], [])
        if ref:
            featured, gallery, files = find_media(ref)
        rows.append({
            "id": p["id"], "name": p.get("name", ""), "ref": ref,
            "featured": featured, "gallery": gallery, "files": files,
            "ok": bool(featured),
            "reason": "" if featured else ("بی‌رفرنس" if not ref else "عکسِ شاخص یافت نشد"),
        })
    return rows


def apply_row_wc(row: dict, title: str = "") -> bool:
    """images محصول را با wc/v3 ست می‌کند: شاخص اول، سپس گالری؛ همراهِ name+alt=عنوانِ محصول.
    (فقط title/alt نوشتنی‌اند؛ caption/description از این راه نه — آن‌ها با اندپوینتِ CRM.)"""
    if not row["ok"]:
        return False
    ids = [row["featured"], *row["gallery"]]
    params = {"consumer_key": _CK, "consumer_secret": _CS}
    for i, mid in enumerate(ids):
        params[f"images[{i}][id]"] = mid
        if title:
            params[f"images[{i}][name]"] = title
            params[f"images[{i}][alt]"] = title
    r = requests.put(f"{_BASE}/wp-json/wc/v3/products/{row['id']}", params=params, timeout=40)
    r.raise_for_status()
    return True


def apply_row_crm(pid: int, dry_run: bool = False) -> dict:
    """اندپوینتِ افزونهٔ CRM را برای درجِ کاملِ عکس + هر ۴ فیلدِ متادیتا صدا می‌زند (سمتِ سرور).
    ورودی sync؛ خروجی dict یا {"ok":False,"reason":...} اگر اندپوینت نبود."""
    if not (config.CRM_TG_URL and config.CRM_TG_TOKEN):
        return {"ok": False, "reason": "crm_disabled"}
    p = {"product_id": int(pid)}
    if dry_run:
        p["dry_run"] = 1
    try:
        r = requests.post(f"{config.CRM_TG_URL}/product-images", params=p,
                          headers={"X-A2-Token": config.CRM_TG_TOKEN, "Accept": "application/json"}, timeout=40)
        if r.status_code == 404:
            return {"ok": False, "reason": "no_endpoint"}
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, dict) else {"ok": False, "reason": "bad_response"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": type(e).__name__}


def apply_row(row: dict, use_crm: bool = True) -> dict:
    """یک محصول را اعمال می‌کند. اگر اندپوینتِ CRM هست → متادیتای کامل (شاملِ description)؛
    وگرنه fallback به wc/v3 (شاخص+گالری+title+alt). خروجی: {done, via, crm_meta, err}."""
    if not row["ok"]:
        return {"done": False, "via": None, "err": row.get("reason")}
    if use_crm:
        res = apply_row_crm(row["id"])
        if res.get("error") is None and res.get("reason") not in (
                "crm_disabled", "no_endpoint", "unreachable") and (
                res.get("featured_set") or res.get("images_updated")):
            return {"done": True, "via": "crm", "crm_meta": True, "err": None}
        # اندپوینت نبود/جواب نداد → fallback به wc/v3
    try:
        apply_row_wc(row, title=row.get("name", ""))
        return {"done": True, "via": "wc", "crm_meta": False, "err": None}
    except Exception as e:  # noqa: BLE001
        return {"done": False, "via": None, "err": type(e).__name__}


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["ok"]]
    return {
        "total": len(rows),
        "with_featured": len(ok),
        "with_gallery": len([r for r in ok if r["gallery"]]),
        "gallery_imgs": sum(len(r["gallery"]) for r in ok),
        "no_media": len([r for r in rows if not r["ok"]]),
    }
