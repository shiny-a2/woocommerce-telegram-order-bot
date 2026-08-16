"""citizen_job.py — جابِ روزانهٔ سینکِ سیتیزن (app.supplier.example → ووکامرس) + گزارشِ اکسل به اپراتور/مالک.

مستقل و ضدِ ریبوت: از طریقِ Scheduled Task «WooCitizenSync» (روزانه + at-boot). توکن از db.meta (لاگینِ /citizen).
گاردها (fail-closed): توکن نبود/منقضی → هشدار به مالک+اپراتور («/citizen بزن») و بدونِ نوشتن.
                      →ناموجود > WT_CITIZEN_MAX_OOS → نگه‌دار + هشدار (ضدِ فاجعهٔ خواندنِ ناقص).
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import urllib.request
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import db          # noqa: E402
db.init()
import config as c          # noqa: E402
import woo                  # noqa: E402
import saati                # noqa: E402
import citizen_sync as cz   # noqa: E402

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
_LOG = os.path.join(_HERE, "data", "citizen.log")
_DATA = os.path.join(_HERE, "data")


def log(msg: str):
    ts = datetime.datetime.now(_TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _tg(method, fields, files=None):
    token = c.TELEGRAM_BOT_TOKEN
    if not token:
        return False
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    for k, (fn, data, ct) in (files or {}).items():
        parts.append((f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                      f"Content-Type: {ct}\r\n\r\n").encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{b}--\r\n".encode("utf-8"))
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return "\"ok\":true" in r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return False


def send_text(chat_id, text):
    return _tg("sendMessage", {"chat_id": str(chat_id), "text": text})


def send_doc(chat_id, path, caption):
    with open(path, "rb") as f:
        data = f.read()
    return _tg("sendDocument", {"chat_id": str(chat_id), "caption": caption},
               {"document": (os.path.basename(path), data,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def _recipients():
    ids = list(c.ADMIN_USER_IDS or [])
    op = getattr(c, "WT_CITIZEN_OPERATOR_ID", 0)
    if op and op not in ids:
        ids.append(op)
    return ids


def alert_owner(text):
    log("ALERT: " + text)
    for oid in (c.ADMIN_USER_IDS or []):
        send_text(oid, text)


async def main() -> int:
    if not c.WT_CITIZEN_ENABLED:
        log("WT_CITIZEN_ENABLED=off → خروج.")
        return 0
    if not saati.logged_in():
        alert_owner("🔑 توکنِ سیتیزن منقضی/غایب است. در ربات دستورِ /citizen را بزن و دوباره لاگین کن. (سینک تا آن‌موقع انجام نشد.)")
        return 1
    try:
        res = await cz.run(woo, apply=False, out_dir=_DATA)   # اول dry-run برای گاردِ دامنه
    except PermissionError:
        alert_owner("🔑 توکنِ سیتیزن رد شد (unauthorized). در ربات /citizen را بزن. سایت دست‌نخورده.")
        return 1
    except Exception as e:  # noqa: BLE001
        alert_owner(f"⛔ سینکِ سیتیزن خطا داد: {type(e).__name__}. سایت دست‌نخورده.")
        return 1
    plan = res["plan"]
    n_oos = len(plan["outofstock"])
    log(f"پلن: {res['summary']}")
    if n_oos > c.WT_CITIZEN_MAX_OOS:
        for oid in _recipients():
            send_doc(oid, res["xlsx"], f"⚠️ سینکِ سیتیزن متوقف شد (احتیاط): →ناموجود={n_oos} از سقفِ {c.WT_CITIZEN_MAX_OOS} گذشت. "
                                       f"احتمالِ خواندنِ ناقص. سایت دست‌نخورده. اگر درست است دستی تأیید کن.")
        log("HOLD: oos بیش از سقف.")
        return 2

    apply = c.WT_CITIZEN_APPLY
    if not apply:
        cap = f"📊 پیش‌نمایشِ روزانهٔ سیتیزن (بدونِ نوشتن)\n{res['summary']}"
        for oid in _recipients():
            send_doc(oid, res["xlsx"], cap)
        log("APPLY=off → فقط گزارش.")
        return 0

    res2 = await cz.run(woo, apply=True, out_dir=_DATA)   # اعمالِ واقعی (تازه‌محاسبه)
    r = res2["result"]
    errs = len(r["errors"])
    log(f"اعمال شد: price={r['price']} instock={r['instock']} oos={r['outofstock']} errors={errs}")
    cap = (f"✅ سینکِ روزانهٔ سیتیزن انجام شد\n\n• قیمت: {r['price']}\n• → موجود: {r['instock']}\n"
           f"• → ناموجود: {r['outofstock']}\n• خطا: {errs}\n\nگزارشِ کامل در اکسل.")
    for oid in _recipients():
        send_doc(oid, res2["xlsx"], cap)
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"[fatal] {e!r}\n{traceback.format_exc()}")
        try:
            alert_owner(f"⛔ جابِ سیتیزن با خطای غیرمنتظره متوقف شد: {type(e).__name__}.")
        except Exception:  # noqa: BLE001
            pass
        rc = 1
    sys.exit(rc)
