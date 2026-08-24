"""pricesync_sync.py — جابِ روزانهٔ سینکِ قیمت/موجودی (کانالِ CAT Group → سایت) + گزارشِ اکسل به اپراتور.

مستقل و ضدِ ریبوت: از طریقِ یک Scheduled Task با triggerِ روزانه + at-boot اجرا می‌شود (بی‌نیاز به ربات).
گاردهای ایمنی (fail-closed) — اگر هرکدام نقض شد، «هیچ نوشتنی» انجام نمی‌شود و به مالک هشدار می‌رود:
  • اسنپ‌شاتِ کانال نبود/خراب بود
  • رفرنسِ کانال < WT_PRICESYNC_MIN_REFS  (خواندنِ ناقص)
  • اسنپ‌شات کهنه‌تر از WT_PRICESYNC_MAX_AGE_H ساعت  (tg-outreach خوابیده)
  • →ناموجود > WT_PRICESYNC_MAX_OOS  یا  کلِ تغییرات > WT_PRICESYNC_MAX_CHANGES  (دامنهٔ مشکوک)

خروجی‌ها: لاگِ data/pricesync.log ، اکسل در data/ ، پیام به اپراتور (WT_PRICESYNC_OPERATOR_ID) و ادمین‌ها.
تست دستی:  .venv\Scripts\python.exe pricesync_sync.py
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time
import urllib.request
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import config as c          # noqa: E402
import woo                  # noqa: E402
import wt_pricesync as ps   # noqa: E402
import wt_pricesync_report as rep  # noqa: E402

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
_LOG = os.path.join(_HERE, "data", "pricesync.log")
_DATA = os.path.join(_HERE, "data")


def log(msg: str):
    ts = datetime.datetime.now(_TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


# ---------- تلگرام (بدونِ وابستگی به پروسهٔ ربات) ----------
def _tg(method: str, fields: dict, files: dict | None = None):
    token = c.TELEGRAM_BOT_TOKEN
    if not token:
        return False, "no token"
    url = f"https://api.telegram.org/bot{token}/{method}"
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    for k, (fn, data, ctype) in (files or {}).items():
        parts.append((f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                      f"Content-Type: {ctype}\r\n\r\n").encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{b}--\r\n".encode("utf-8"))
    req = urllib.request.Request(url, data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return True, r.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        body = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else ""
        return False, f"{type(e).__name__} {str(e)[:150]} {body}"


def send_text(chat_id, text):
    return _tg("sendMessage", {"chat_id": str(chat_id), "text": text})


def send_doc(chat_id, path, caption):
    with open(path, "rb") as f:
        data = f.read()
    return _tg("sendDocument", {"chat_id": str(chat_id), "caption": caption},
               {"document": (os.path.basename(path), data,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def _owners():
    return list(c.ADMIN_USER_IDS or [])


def alert_owner(text):
    log("ALERT: " + text.replace("\n", " | "))
    for oid in _owners():
        send_text(oid, text)


def _ts():
    return datetime.datetime.now(_TEHRAN).strftime("%Y%m%d-%H%M%S")


async def main() -> int:
    if not c.WT_PRICESYNC_ENABLED:
        log("WT_PRICESYNC_ENABLED=off → خروجِ بی‌صدا.")
        return 0

    # ۱) اسنپ‌شاتِ کانال + گاردهای سلامتِ داده
    try:
        prices, meta = ps.load_channel_file()
    except Exception as e:  # noqa: BLE001
        alert_owner(f"⛔ سینکِ قیمت لغو شد: فایلِ کانال خوانده نشد ({type(e).__name__}). سایت دست‌نخورده.")
        return 1
    n_ref = len(prices)
    age_h = (time.time() - (meta.get("updated_ts") or 0)) / 3600.0
    if n_ref < c.WT_PRICESYNC_MIN_REFS:
        alert_owner(f"⛔ سینکِ قیمت لغو شد: کانال فقط {n_ref} رفرنس دارد (حدِ {c.WT_PRICESYNC_MIN_REFS}). "
                    f"احتمالِ خواندنِ ناقص. سایت دست‌نخورده.")
        return 1
    if age_h > c.WT_PRICESYNC_MAX_AGE_H:
        alert_owner(f"⛔ سینکِ قیمت لغو شد: آخرین خواندنِ کانال {age_h:.0f} ساعت پیش بوده "
                    f"(حدِ {c.WT_PRICESYNC_MAX_AGE_H}h). سرویسِ tg-outreach را چک کن. سایت دست‌نخورده.")
        return 1

    # ۲) واکشیِ محصولاتِ برند + پلن (یک‌بار)
    products = await ps.fetch_brand_products(woo)
    plan = ps.plan_changes(prices, products)
    log(f"پلن: {ps.summarize(plan)} | کانال={n_ref} رفرنس، اسنپ‌شات={age_h:.1f}h، محصولاتِ برند={len(products)}")

    # ۳) قانونِ مالک: بدونِ سقفِ تعدادی — چه ۲۰۰ چه ۷۰۰ اعمال کن.
    #    گاردِ خواندنِ ناقص همچنان برقرار است (MIN_REFS در بالا + کهنگیِ اسنپ‌شات)،
    #    پس فاجعهٔ «همه‌چیز اشتباهاً ناموجود» با کمبودِ رفرنس‌های کانال گرفته می‌شود، نه با شمارشِ تغییرات.

    # ۴) اعمال (یا فقط گزارش اگر APPLY خاموش)
    apply = c.WT_PRICESYNC_APPLY
    result = await ps.apply_plan(woo, plan) if apply else None
    xlsx = os.path.join(_DATA, f"pricesync-{'applied' if apply else 'dryrun'}-{_ts()}.xlsx")
    rep.build_excel(plan, products, meta, xlsx, applied=apply, apply_result=result)

    if not apply:
        cap = f"📊 پیش‌نمایشِ روزانهٔ سینک (بدونِ نوشتن روی سایت)\n{ps.summarize(plan)}"
        ok, msg = send_doc(c.WT_PRICESYNC_OPERATOR_ID, xlsx, cap)
        log(f"گزارشِ dry-run به اپراتور: {'ok' if ok else 'FAIL ' + msg}")
        for oid in _owners():
            send_doc(oid, xlsx, cap)
        return 0

    r = result
    done = r["price"] + r["instock"] + r["outofstock"]
    errs = len(r["errors"])
    log(f"اعمال شد: price={r['price']} instock={r['instock']} oos={r['outofstock']} errors={errs}")
    if done == 0 and errs:
        send_doc(_owners()[0] if _owners() else c.WT_PRICESYNC_OPERATOR_ID, xlsx,
                 f"⛔ سینکِ قیمت: همهٔ {errs} نوشتن شکست خورد (کلیدِ API یا سایت). سایت دست‌نخورده.")
        alert_owner(f"⛔ سینکِ قیمت: همهٔ {errs} نوشتن شکست خورد. کلیدِ Read/Write یا سایت را چک کن.")
        return 1
    cap = (f"✅ سینکِ روزانهٔ قیمت/موجودی انجام شد\n\n"
           f"• قیمت: {r['price']}\n• → موجود: {r['instock']}\n• → ناموجود: {r['outofstock']}\n"
           f"• خطا: {errs}\n\nجزئیات در فایلِ اکسل.")
    ok, msg = send_doc(c.WT_PRICESYNC_OPERATOR_ID, xlsx, cap)
    log(f"گزارش به اپراتور ({c.WT_PRICESYNC_OPERATOR_ID}): {'ok' if ok else 'FAIL ' + msg}")
    for oid in _owners():
        send_doc(oid, xlsx, cap)
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"[fatal] {e!r}\n{traceback.format_exc()}")
        try:
            alert_owner(f"⛔ سینکِ قیمت با خطای غیرمنتظره متوقف شد: {type(e).__name__}. سایت احتمالاً دست‌نخورده.")
        except Exception:  # noqa: BLE001
            pass
        rc = 1
    sys.exit(rc)
