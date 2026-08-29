"""sales_attrib_job.py — کارنامهٔ فروشِ اپراتورهای CRM (نسیم) → گروهِ گزارش (روزانه/دستی).

انتساب: «تماس→خرید» ۷روزه (sales_attrib). فقط‌خواندنی؛ کارتِ HTML به گروهِ گزارش + مدیران.
مستقل و ضدِ ریبوت: Scheduled Task «WooSalesAttrib». روشن/خاموش: WT_SALES_ATTRIB_ENABLED.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import db          # noqa: E402
db.init()
import config as c          # noqa: E402
import woo                  # noqa: E402
import sales_attrib as sa   # noqa: E402

_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
_LOG = os.path.join(_HERE, "data", "sales_attrib.log")


def log(msg: str):
    ts = datetime.datetime.now(_TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def send_html(chat_id, text) -> bool:
    token = c.TELEGRAM_BOT_TOKEN
    if not token:
        return False
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text,
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return "\"ok\":true" in r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return False


def _report_targets() -> list:
    """گروهِ گزارش/کار → REPORTS_CHAT_ID → پیویِ مدیران."""
    ids = []
    wg = db.get_meta("work_group")
    if wg:
        try:
            ids.append(int(wg))
        except ValueError:
            pass
    if getattr(c, "REPORTS_CHAT_ID", 0) and c.REPORTS_CHAT_ID not in ids:
        ids.append(c.REPORTS_CHAT_ID)
    if not ids:
        ids = list(c.ADMIN_USER_IDS or [])
    return ids


async def main() -> int:
    if not getattr(c, "WT_SALES_ATTRIB_ENABLED", False):
        log("WT_SALES_ATTRIB_ENABLED=off → خروج.")
        return 0
    days = getattr(c, "WT_SALES_ATTRIB_DAYS", 30)
    targets = _report_targets()
    for op_id in sa.OPERATORS:
        try:
            st = await sa.run(woo, op_id, days=days)
        except Exception as e:  # noqa: BLE001
            log(f"خطا برای اپراتور {op_id}: {type(e).__name__}: {str(e)[:120]}")
            continue
        log(f"{st['op_name']}: اساین={st['assigned']} تماس={st['contacted']} "
            f"منتسب={len(st['attributed'])} درآمد={int(st['revenue_attributed'])} از {st['orders_total']} سفارش")
        text = sa.report_text(st)
        for tid in targets:
            send_html(tid, text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
