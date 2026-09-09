"""imgpipe.py — خطِ لولهٔ عکسِ محصول (بیسِ Stage-0).

مسیر:  مبدأ (سایت) → خامِ نام‌گذاری‌شده → [اختیاری: اسکریپتِ فوتوشاپ] → کتابخانهٔ رسانهٔ وردپرس → اتصال به محصول با رفرنس.

اصولِ طراحی:
  • هیچ عکسی به‌صورتِ دائمی اینجا ذخیره نمی‌شود؛ فقط بچِ در جریان (و در حالتِ direct اصلاً صفر).
  • نام‌گذاری دقیقاً طبقِ قراردادِ موجود: شاخص = «{ref}.jpg» و گالری = «{ref}-1.jpg، {ref}-2.jpg …»
    (هم اسکریپتِ فوتوشاپِ مالک همین را می‌سازد، هم mediaimg.find_media همین را می‌خواند.)
  • آپلود از طریقِ wp-cli روی خودِ سرور (کلیدِ ووکامرس اجازهٔ نوشتنِ wp/v2/media را ندارد — تأییدشده).
  • پیش‌فرض dry-run؛ هیچ چیزی روی سایت نوشته نمی‌شود مگر apply=True.
  • هیچ AI/LLM در این ماژول نیست.

حالت‌ها (WT_IMGPIPE_MODE):
  direct  — سرور خودش URLِ مبدأ را می‌گیرد و مستقیم وارد رسانه می‌کند (بدونِ فوتوشاپ، صفر ذخیره‌سازیِ محلی).
  handoff — خام‌ها در inbox ریخته می‌شوند؛ مالک اسکریپتِ فوتوشاپش را روی inbox→outbox اجرا می‌کند؛
            سپس فایل‌های outbox آپلود می‌شوند. (اسکریپتِ فوتوشاپ دست‌نخورده می‌ماند.)
"""
from __future__ import annotations

import os
import re
import subprocess
import time

import config
import mediaimg

_HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(_HERE, "data", "imgpipe", "inbox")
OUTBOX = os.path.join(_HERE, "data", "imgpipe", "outbox")
SSH_KEY = os.path.join(_HERE, ".ssh", "jeweltime_ed25519")
SSH_HOST = "root@server.example"
SERVER_SCRIPT = "/home/siteuser/wp_media_import.sh"
SITE_USER = "siteuser"
STAGING = "/home/imgdrop/staging"      # جاییکه ایستگاهِ طراحی خروجی‌های ادیت‌شده را می‌ریزد
_EXT_OK = ("jpg", "jpeg", "png", "webp")
_SUFFIX_RE = re.compile(r"^(.+)-(\d+)$")


# ---------------- source adapter interface ----------------
class SourceAdapter:
    """قراردادِ مبدأ. برای هر سایت یک زیرکلاس نوشته می‌شود (پس از تعیینِ سایت‌ها).

    discover(refs) → {ref: [url1, url2, url3, ...]}  به‌ترتیبِ اولویت (اولی = عکسِ اصلی).
    هیچ‌چیز نمی‌نویسد؛ فقط کشف می‌کند.
    """
    name = "base"

    def discover(self, refs: list[str]) -> dict[str, list[str]]:
        raise NotImplementedError


# ---------------- targets (محصولاتِ خودمان) ----------------
def targets(brand_term: str | None = None, limit: int = 0) -> list[dict]:
    """محصولاتِ هدفِ یک برند: [{id, name, ref, images_count}].

    فعلاً فقط «بی‌عکس‌ها» (mediaimg.fetch_noimage_products). اگر بعداً «کم‌عکس‌ها» هم لازم شد،
    یک fetch جداگانه اضافه می‌شود — عمداً حدس نمی‌زنیم که mediaimg تابعِ دیگری دارد.
    """
    rows = []
    prods = mediaimg.fetch_noimage_products(brand_term)
    for p in prods:
        ref = mediaimg.product_ref(p)
        if not ref:
            continue
        rows.append({"id": p.get("id"), "name": p.get("name", ""), "ref": ref,
                     "images_count": len(p.get("images") or [])})
        if limit and len(rows) >= limit:
            break
    return rows


# ---------------- naming ----------------
def raw_name(ref: str, index: int, ext: str = "jpg") -> str:
    """نامِ خامِ ورودیِ فوتوشاپ: index=0 → «{ref}.jpg» (پایه، الزامی)، index≥1 → «{ref}-{index}.jpg»."""
    ext = (ext or "jpg").lower().lstrip(".")
    if ext not in _EXT_OK:
        ext = "jpg"
    return f"{ref}.{ext}" if index == 0 else f"{ref}-{index}.{ext}"


def refs_in_folder(folder: str) -> list[str]:
    """رفرنس‌های موجود در یک پوشه — دقیقاً با همان منطقِ گروه‌بندیِ اسکریپتِ فوتوشاپ:

    «{x}-{N}» فقط وقتی «پسوندِ گالری» حساب می‌شود که خودِ «{x}» هم در پوشه باشد؛ وگرنه کلِ نام یک رفرنسِ
    مستقل است. این دقیقاً همان چیزی است که از ابهامِ رفرنس‌های خط‌تیره‌دار (مثلِ 16-6018-13-007) جلوگیری می‌کند.
    """
    if not os.path.isdir(folder):
        return []
    stems = set()
    for f in os.listdir(folder):
        if f.lower().startswith("logo."):      # همان استثنای اسکریپتِ فوتوشاپ
            continue
        stem, _, ext = f.rpartition(".")
        if stem and ext.lower() in _EXT_OK:
            stems.add(stem)
    refs = set()
    for s in stems:
        m = _SUFFIX_RE.match(s)
        refs.add(m.group(1) if (m and m.group(1) in stems) else s)
    return sorted(refs)


# ---------------- stage: خام‌ها → inbox (حالتِ handoff) ----------------
def stage_to_inbox(plan_rows: list[dict], max_images: int = 3, apply: bool = False) -> dict:
    """خام‌ها را با نامِ درست در inbox می‌ریزد تا اسکریپتِ فوتوشاپ رویشان اجرا شود.

    plan_rows: [{ref, urls: [...]}]. خروجی: آمار. apply=False → فقط شمارش (چیزی دانلود نمی‌شود).
    """
    import requests
    os.makedirs(INBOX, exist_ok=True)
    stat = {"refs": 0, "files": 0, "skipped": 0, "errors": 0}
    for row in plan_rows:
        ref, urls = row.get("ref"), (row.get("urls") or [])[:max_images]
        if not ref or not urls:
            stat["skipped"] += 1
            continue
        stat["refs"] += 1
        for i, url in enumerate(urls):
            ext = (url.split("?")[0].rsplit(".", 1)[-1] or "jpg").lower()
            dest = os.path.join(INBOX, raw_name(ref, i, ext))
            if os.path.exists(dest):
                continue
            if not apply:
                stat["files"] += 1
                continue
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                if len(r.content) < 2000:
                    stat["errors"] += 1
                    continue
                with open(dest, "wb") as f:
                    f.write(r.content)
                stat["files"] += 1
            except Exception as e:  # noqa: BLE001
                print(f"[imgpipe] دانلودِ {ref} ({url[:60]}) نشد: {type(e).__name__}")
                stat["errors"] += 1
    return stat


# ---------------- upload: → کتابخانهٔ رسانه (روی سرور) ----------------
def _ssh(cmd: str, stdin_text: str = "", timeout: int = 900) -> tuple[int, str, str]:
    p = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=20", SSH_HOST, cmd],
                       input=stdin_text.encode("utf-8"), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def upload_urls(pairs: list[tuple[str, str]], apply: bool = False) -> dict:
    """حالتِ direct (بدونِ فوتوشاپ): [(filename, url)] را مستقیم روی سرور وارد رسانه می‌کند.

    filename باید نامِ نهاییِ دقیق باشد؛ با raw_name(ref, i) ساخته می‌شود (چون ref را می‌دانیم، ابهامی نیست).
    صفر ذخیره‌سازیِ محلی: دانلود روی خودِ سرور انجام می‌شود.
    """
    if not pairs:
        return {"ok": 0, "err": 0, "rows": []}
    if not apply:
        return {"ok": 0, "err": 0, "rows": [], "dry_run": True, "would_import": len(pairs),
                "sample": [p[0] for p in pairs[:5]]}
    lines = "\n".join(f"{fn}\t{u}" for fn, u in pairs) + "\n"
    cmd = f'su -s /bin/bash {SITE_USER} -c "{SERVER_SCRIPT}"'
    rc, out, err = _ssh(cmd, lines)
    return _parse_import_output(out, err, rc)


def upload_folder(folder: str = OUTBOX, apply: bool = False) -> dict:
    """حالتِ handoff: فایل‌های ادیت‌شدهٔ outbox را به سرور می‌فرستد و وارد رسانه می‌کند."""
    if not os.path.isdir(folder):
        return {"ok": 0, "err": 0, "rows": [], "note": f"folder not found: {folder}"}
    files = [f for f in sorted(os.listdir(folder)) if f.rsplit(".", 1)[-1].lower() in _EXT_OK]
    if not files:
        return {"ok": 0, "err": 0, "rows": [], "note": "outbox empty"}
    if not apply:
        return {"ok": 0, "err": 0, "rows": [], "dry_run": True, "would_import": len(files),
                "sample": files[:5]}
    remote_dir = f"/tmp/imgpipe_{int(time.time())}"
    _ssh(f"mkdir -p {remote_dir} && chown {SITE_USER}:{SITE_USER} {remote_dir}")
    scp = ["scp", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
    scp += [os.path.join(folder, f) for f in files] + [f"{SSH_HOST}:{remote_dir}/"]
    p = subprocess.run(scp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    if p.returncode != 0:
        return {"ok": 0, "err": len(files), "rows": [],
                "note": "scp failed: " + p.stderr.decode("utf-8", "replace")[:200]}
    _ssh(f"chown -R {SITE_USER}:{SITE_USER} {remote_dir}")
    # نامِ فایل عیناً حفظ می‌شود (خروجیِ فوتوشاپ از قبل درست نام‌گذاری شده) — هیچ بازسازیِ رفرنس نمی‌کنیم
    lines = "".join(f"{f}\t{remote_dir}/{f}\n" for f in files)
    cmd = f'su -s /bin/bash {SITE_USER} -c "{SERVER_SCRIPT}"'
    rc, out, err = _ssh(cmd, lines)
    res = _parse_import_output(out, err, rc)
    _ssh(f"rm -rf {remote_dir}")
    return res


# ---------------- staging: بسته‌های رسیده از ایستگاهِ طراحی ----------------
def list_batches() -> list[dict]:
    """بسته‌های ادیت‌شده‌ای که ایستگاهِ طراحی روی سرور گذاشته: [{batch, files}] (قدیمی‌ترین اول)."""
    rc, out, _ = _ssh(f"for d in {STAGING}/*/; do [ -d \"$d\" ] && "
                      f"echo \"$(basename $d)\t$(ls -1 $d | wc -l)\"; done 2>/dev/null")
    rows = []
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) == 2 and parts[1].strip().isdigit():
            rows.append({"batch": parts[0].strip(), "files": int(parts[1].strip())})
    return sorted(rows, key=lambda r: r["batch"])


def import_batch(batch: str, apply: bool = False, cleanup: bool = True) -> dict:
    """یک بستهٔ staging را وارد کتابخانهٔ رسانه می‌کند (نامِ فایل عیناً حفظ می‌شود).

    گیرندهٔ روی سرور فقط فایل می‌گذارد و هیچ دسترسیِ نوشتنی به وردپرس ندارد؛ واردکردن عمداً
    اینجا و با کاربرِ سایت انجام می‌شود.
    """
    if not re.fullmatch(r"[0-9A-Za-z._:-]{1,64}", batch or ""):
        return {"ok": 0, "err": 0, "rows": [], "note": "bad batch name"}
    remote = f"{STAGING}/{batch}"
    rc, out, _ = _ssh(f"ls -1 {remote} 2>/dev/null")
    files = [f.strip() for f in out.splitlines()
             if f.strip() and f.strip().rsplit(".", 1)[-1].lower() in _EXT_OK]
    if not files:
        return {"ok": 0, "err": 0, "rows": [], "note": f"batch empty: {batch}"}
    if not apply:
        return {"ok": 0, "err": 0, "rows": [], "dry_run": True,
                "would_import": len(files), "sample": files[:5]}
    _ssh(f"chown -R {SITE_USER}:{SITE_USER} {remote} && chmod -R u+rwX {remote}")
    lines = "".join(f"{f}\t{remote}/{f}\n" for f in files)
    cmd = f'su -s /bin/bash {SITE_USER} -c "{SERVER_SCRIPT}"'
    rc, out, err = _ssh(cmd, lines)
    res = _parse_import_output(out, err, rc)
    res["batch"] = batch
    if cleanup and res["ok"] and not res["err"]:
        _ssh(f"rm -rf {remote}")
        res["cleaned"] = True
    return res


def _parse_import_output(out: str, err: str, rc: int) -> dict:
    rows, ok, bad = [], 0, 0
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[0] == "OK":
            rows.append({"filename": parts[1], "attachment_id": int(parts[2])})
            ok += 1
        elif len(parts) >= 3 and parts[0] == "ERR":
            rows.append({"filename": parts[1], "error": parts[2]})
            bad += 1
    return {"ok": ok, "err": bad, "rows": rows, "rc": rc, "stderr": err.strip()[-300:]}


# ---------------- attach: رسانه → محصول (با رفرنس) ----------------
def attach(products: list[dict], apply: bool = False) -> list[dict]:
    """رسانهٔ هر رفرنس را پیدا و شاخص/گالری را روی محصول ست می‌کند (از مسیرِ mediaimg).

    products: [{id, name, ref}] — دقیقاً همان چیزی که targets() می‌دهد.
    بدونِ عکسِ شاخص هیچ چیزی ست نمی‌شود (قاعدهٔ mediaimg؛ گالریِ بی‌شاخص بی‌معنی است).
    """
    out = []
    for p in products:
        ref = (p.get("ref") or "").strip()
        pid = p.get("id")
        if not ref or not pid:
            out.append({"ref": ref, "id": pid, "status": "missing_ref_or_id"})
            continue
        try:
            featured, gallery, files = mediaimg.find_media(ref)
        except Exception as e:  # noqa: BLE001
            out.append({"ref": ref, "id": pid, "status": "media_lookup_failed", "err": type(e).__name__})
            continue
        rec = {"ref": ref, "id": pid, "name": p.get("name", ""),
               "featured": featured, "gallery": gallery, "files": files, "ok": bool(featured)}
        if not featured:
            rec["status"] = "no_featured_media"
            out.append(rec)
            continue
        if not apply:
            rec["status"] = "would_attach"
            out.append(rec)
            continue
        try:
            mediaimg.apply_row_wc(rec, title=rec["name"])
            rec["status"] = "attached"
        except Exception as e:  # noqa: BLE001
            rec["status"] = "attach_failed"
            rec["err"] = type(e).__name__
        out.append(rec)
    return out


def summary(rows: list[dict]) -> str:
    ok = sum(1 for r in rows if r.get("status") in ("attached", "would_attach"))
    return f"{ok}/{len(rows)} رفرنس آمادهٔ اتصال."
