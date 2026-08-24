"""jewel_job.py — جابِ روزانهٔ سینکِ jeweltime → جواهریان + گزارشِ اکسل به مالک/اپراتور.

مستقل و ضدِ ریبوت: Scheduled Task «WooJewelSync» (روزانه + at-boot). خواندنِ مبدأ read-only با SSH.
گاردِ fail-closed: اگر فهرستِ jeweltime خالی برگردد (SSH/شبکه/خطای خواندن) → دست نگه‌دار، هشدار،
                    سایت دست‌نخورده. قیمت هرگز نوشته نمی‌شود (فقط گزارش می‌شود).
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
import jewel_sync as js     # noqa: E402

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
_LOG = os.path.join(_HERE, "data", "jewel.log")
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
    if not getattr(c, "WT_JEWEL_ENABLED", False):
        log("WT_JEWEL_ENABLED=off → خروج.")
        return 0
    try:
        res = await js.run(woo, apply=False, out_dir=_DATA)      # اول dry-run
    except Exception as e:  # noqa: BLE001
        alert_owner(f"⛔ سینکِ jeweltime خطا داد: {type(e).__name__}. سایت دست‌نخورده.")
        return 1
    log(f"پلن: {res['summary']} | jeweltime={res['jt_count']} جواهریان={res['jav_count']}")

    # گاردِ خواندنِ ناقص: اگر فهرستِ مبدأ خالی بود → دست نگه‌دار (SSH/شبکه/خطا).
    if res["jt_count"] == 0:
        for oid in _recipients():
            send_doc(oid, res["xlsx"], "⚠️ سینکِ jeweltime متوقف شد: فهرستِ مبدأ خالی برگشت (SSH/شبکه/خطای خواندن). سایت دست‌نخورده.")
        log("HOLD: فهرستِ jeweltime خالی.")
        return 2

    if not getattr(c, "WT_JEWEL_APPLY", False):
        cap = f"📊 پیش‌نمایشِ روزانهٔ jeweltime → جواهریان (بدونِ نوشتن)\n{res['summary']}"
        for oid in _recipients():
            send_doc(oid, res["xlsx"], cap)
        log("APPLY=off → فقط گزارش.")
        return 0

    res2 = await js.run(woo, apply=True, out_dir=_DATA)          # اعمالِ واقعی (تازه‌محاسبه)
    r = res2["result"]
    errs = len(r["errors"])
    log(f"اعمال شد: stock={r['stock']} errors={errs}")
    cap = (f"✅ سینکِ روزانهٔ jeweltime → جواهریان انجام شد\n\n"
           f"• موجودی/تعدادِ نوشته‌شده: {r['stock']}\n"
           f"• اختلافِ قیمت (فقط گزارش): {len(res2['plan']['price_diff'])}\n"
           f"• خطا: {errs}\n\nگزارشِ کامل در اکسل (شیتِ «اختلاف-قیمت» را ببین).")
    for oid in _recipients():
        send_doc(oid, res2["xlsx"], cap)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
