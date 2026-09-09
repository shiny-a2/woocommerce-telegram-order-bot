"""imgseo.py — ساختِ متادیتای سئو برای رسانه و محصول، و نوشتنِ آن روی سایت با wp-cli.

چرا wp-cli: کلیدِ ووکامرس اجازهٔ نوشتنِ `wp/v2/media` را ندارد (تأییدشده)، ولی wp-cli روی خودِ سرور
دسترسیِ کامل دارد و افزونهٔ سئوی سایت هم Rank Math (به‌همراه Pro) است.

اصول:
  • متن‌ها **قالبی و قطعی** ساخته می‌شوند — هیچ AI/LLM ای اینجا نیست، پس خروجی تکرارپذیر است.
  • فیلدِ خالی هرگز نوشته نمی‌شود (اسکریپتِ سرور هم همین را رعایت می‌کند)؛ یعنی این ماژول
    هیچ‌وقت متادیتای دستیِ موجود را با رشتهٔ خالی پاک نمی‌کند.
  • پیش‌فرض dry-run؛ بدونِ apply=True هیچ چیزی روی سایت نوشته نمی‌شود.
  • فقط متای سئو نوشته می‌شود؛ عنوان/قیمت/موجودیِ محصول دست نمی‌خورد.
"""
from __future__ import annotations

import re

import imgpipe

SITE_NAME = "فروشگاه"
SERVER_SCRIPT = "/home/siteuser/wp_media_seo.sh"

# سقف‌های متعارفِ سئو (بریدنِ کلمه‌ای، نه وسطِ کلمه)
TITLE_MAX = 60
DESC_MAX = 155

_SUFFIX_RE = re.compile(r"^(.+)-(\d+)$")
_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)$", re.I)


# ---------------------------------------------------------------- helpers ----
def clip(text: str, limit: int) -> str:
    """بریدنِ متن در مرزِ کلمه، بدونِ سه‌نقطهٔ زشت وسطِ عبارت.

    دنبالهٔ آویزان هم پاک می‌شود: کسرهٔ اضافه («ضمانتِ») یا حرفِ ربطِ تنها («با»، «و»، «از»…)
    که بعد از برش بی‌معنی می‌ماند.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) > limit:
        cut = text[:limit]
        sp = cut.rfind(" ")
        text = (cut[:sp] if sp > limit * 0.6 else cut)
    text = text.strip(" ،-–—|")
    text = re.sub(r"[َُِْ]$", "", text)          # اعرابِ آویزان (کسرهٔ اضافه)
    text = re.sub(r"\s+(?:با|و|از|در|به|تا|برای|که)$", "", text)     # حرفِ ربطِ تنها در انتها
    return text.strip(" ،-–—|")


def split_ref(filename: str, known_refs: set[str] | None = None) -> tuple[str, int]:
    """«{ref}.jpg» → (ref, 0) و «{ref}-N.jpg» → (ref, N).

    ⚠️ رفرنس‌های ما خودشان خط‌تیره دارند (مثلِ 16-6018-13-007)، پس «-N» فقط وقتی شمارهٔ گالری
    است که پایه‌اش در مجموعهٔ رفرنس‌های شناخته‌شده باشد. اگر مجموعه ندهیم، محافظه‌کارانه کلِ نام
    را رفرنس می‌گیریم — همان قاعده‌ای که اسکریپتِ فوتوشاپ و refs_in_folder دارند.
    """
    stem = _EXT_RE.sub("", filename.strip())
    m = _SUFFIX_RE.match(stem)
    if m and known_refs and m.group(1) in known_refs:
        return m.group(1), int(m.group(2))
    return stem, 0


def _clean(s: str) -> str:
    """پاک‌سازیِ متن برای TSV — تب/خطِ جدید نباید قالبِ ورودیِ سرور را بشکند."""
    return re.sub(r"[\t\r\n]+", " ", str(s or "")).strip()


# ------------------------------------------------------------ media meta ----
def media_meta(ref: str, index: int, product_name: str = "", brand: str = "") -> dict:
    """متادیتای یک فایلِ رسانه. index=0 یعنی عکسِ شاخص، ۱ به‌بالا گالری."""
    subject = _clean(product_name) or _clean(f"ساعت مچی {brand} مدل {ref}".replace("  ", " "))
    if index == 0:
        alt = subject
        title = subject
    else:
        alt = f"{subject} — نمای {index + 1}"
        title = f"{subject} — تصویر {index + 1}"
    caption = subject
    desc = f"تصویرِ {subject} در {SITE_NAME}. کدِ محصول: {ref}."
    return {
        "alt": clip(alt, 125),
        "title": clip(title, 125),
        "caption": clip(caption, 125),
        "description": clip(desc, 200),
    }


def product_meta(product_name: str, ref: str = "", brand: str = "") -> dict:
    """متای Rank Math برای صفحهٔ محصول."""
    name = _clean(product_name) or _clean(f"ساعت مچی {brand} مدل {ref}")
    # نردبانِ عنوان: بلندترین قالبی که در حدِ مجاز جا شود برنده است — تا هیچ‌وقت نیمهٔ یک
    # عبارت («… خرید با ضمانتِ») در عنوان نماند.
    title = ""
    for candidate in (f"{name} | خرید با ضمانتِ اصالت – {SITE_NAME}",
                      f"{name} | خرید از {SITE_NAME}",
                      f"{name} | {SITE_NAME}",
                      name):
        if len(candidate) <= TITLE_MAX:
            title = candidate
            break
    if not title:
        title = clip(name, TITLE_MAX)
    # رفرنس پرارزش‌ترین کلیدواژهٔ عنوان است (کاربر دقیقاً همان را سرچ می‌کند)؛ اگر برشِ نامِ
    # بلند آن را انداخته بود، برش را کوتاه‌تر می‌کنیم تا رفرنس سرِ جایش برگردد.
    if ref and ref not in title:
        title = (clip(name, max(20, TITLE_MAX - len(ref) - 1)) + " " + ref).strip()
    desc = clip(
        f"قیمت و خرید {name} با ضمانتِ اصالتِ کالا از {SITE_NAME}. "
        f"مشاهدهٔ مشخصات، تصاویر و قیمتِ روز"
        + (f" — کدِ محصول {ref}." if ref else "."),
        DESC_MAX,
    )
    return {"title": title, "description": desc, "focus": clip(name, 60)}


# --------------------------------------------------------------- writers ----
def _run(mode: str, lines: list[str], apply: bool, flag: str = "") -> dict:
    if not lines:
        return {"ok": 0, "err": 0, "skip": 0, "rows": []}
    if not apply:
        return {"ok": 0, "err": 0, "skip": 0, "rows": [], "dry_run": True,
                "would_write": len(lines), "sample": lines[:3]}
    inner = f"{SERVER_SCRIPT} {mode} {flag}".strip()
    cmd = f'su -s /bin/bash {imgpipe.SITE_USER} -c "{inner}"'
    rc, out, err = imgpipe._ssh(cmd, "\n".join(lines) + "\n")
    ok = bad = skipped = 0
    rows = []
    for ln in out.splitlines():
        parts = ln.split("\t")
        if parts[0] == "OK" and len(parts) >= 2:
            rows.append({"id": parts[1], "status": "ok"}); ok += 1
        elif parts[0] == "SKIP" and len(parts) >= 3:
            rows.append({"id": parts[1], "status": parts[2]}); skipped += 1
        elif parts[0] == "ERR" and len(parts) >= 3:
            rows.append({"id": parts[1], "status": parts[2]}); bad += 1
    return {"ok": ok, "err": bad, "skip": skipped, "rows": rows,
            "rc": rc, "stderr": err.strip()[-300:]}


def apply_media_seo(items: list[dict], apply: bool = False, only_empty: bool = False) -> dict:
    """items: [{attachment_id, ref, index, product_name?, brand?}] → نوشتنِ alt/title/caption/description.

    only_empty=True یعنی فیلدی که از قبل مقدار دارد دست نخورَد (برای رسانهٔ قدیمی که شاید
    متادیتای دستی دارد؛ برای آپلودهای تازهٔ خودمان لازم نیست).
    """
    lines = []
    for it in items:
        aid = it.get("attachment_id")
        if not aid:
            continue
        m = media_meta(it.get("ref", ""), int(it.get("index", 0)),
                       it.get("product_name", ""), it.get("brand", ""))
        lines.append("\t".join([str(aid), m["alt"], m["title"], m["caption"], m["description"]]))
    return _run("media", lines, apply, "--only-empty" if only_empty else "")


def apply_product_seo(items: list[dict], apply: bool = False, force: bool = False) -> dict:
    """items: [{product_id, name, ref?, brand?}] → نوشتنِ متای Rank Math.

    ⚠️ پیش‌فرض بازنویسی نمی‌کند. بیشترِ محصولاتِ سایت از قبل عنوان/توضیحِ سئوی دستی دارند؛
    force=True فقط وقتی که مالک صریحاً بخواهد همه‌شان با قالبِ ما جایگزین شوند.
    """
    lines = []
    for it in items:
        pid = it.get("product_id") or it.get("id")
        if not pid:
            continue
        m = product_meta(it.get("name", ""), it.get("ref", ""), it.get("brand", ""))
        lines.append("\t".join([str(pid), m["title"], m["description"], m["focus"]]))
    return _run("product", lines, apply, "--force" if force else "")


# ------------------------------------------------------------ convenience ----
def plan_media_from_import(import_rows: list[dict], ref_to_product: dict[str, dict],
                           brand: str = "") -> list[dict]:
    """خروجیِ آپلود (rows از imgpipe) را به ورودیِ apply_media_seo تبدیل می‌کند.

    import_rows : [{filename, attachment_id}]  — دقیقاً خروجیِ imgpipe.upload_*
    ref_to_product : {ref: {"id":…, "name":…}} — برای اینکه alt همان عنوانِ محصول باشد.
    """
    known = set(ref_to_product.keys())
    out = []
    for r in import_rows:
        if "attachment_id" not in r:
            continue
        ref, idx = split_ref(r["filename"], known)
        prod = ref_to_product.get(ref) or {}
        out.append({"attachment_id": r["attachment_id"], "ref": ref, "index": idx,
                    "product_name": prod.get("name", ""), "brand": brand})
    return out
