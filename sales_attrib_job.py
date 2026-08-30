"""sales_attrib_job.py — کارنامهٔ فروشِ اپراتورهای CRM (کارشناسِ فروش) → گروهِ گزارش (روزانه/دستی).

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


def _group_targets() -> list:
    """گروهِ گزارشاتِ روزانه: work_group → REPORTS_CHAT_ID → (اگر هیچ‌کدام) پیویِ مدیران."""
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


def _manager_targets() -> list:
    """پی‌ویِ مدیر(ها) — مقصدِ محرمانهٔ مبالغِ فروش."""
    return list(c.ADMIN_USER_IDS or [])


async def main() -> int:
    if not getattr(c, "WT_SALES_ATTRIB_ENABLED", False):
        log("WT_SALES_ATTRIB_ENABLED=off → خروج.")
        return 0
    days = getattr(c, "WT_SALES_ATTRIB_DAYS", 30)
    group_ids = _group_targets()
    manager_ids = _manager_targets()
    for op_id in sa.OPERATORS:
        try:
            st = await sa.run_daily(woo, op_id, month_days=days)
        except Exception as e:  # noqa: BLE001
            log(f"خطا برای اپراتور {op_id}: {type(e).__name__}: {str(e)[:120]}")
            continue
        # لاگِ محلی می‌تواند مبلغ داشته باشد (فایلِ سرور، نه گروه)
        log(f"{st['op_name']} | امروز: کار={st['activity']['phones_worked']} "
            f"فعالیت={st['activity']['total_actions']} منتسب={len(st['attr_today'])} "
            f"درآمدِ امروز={int(st['rev_today'])} | ماه: منتسب={len(st['attr_month'])} "
            f"درآمدِ ماه={int(st['rev_month'])}")
        # ۱) کارتِ کاملِ روزانه (بدونِ مبلغ) → گروهِ گزارشات
        group_text = sa.report_group_daily(st)
        for tid in group_ids:
            send_html(tid, group_text)
        # ۲) مبالغِ فروش (محرمانه) → فقط پی‌ویِ مدیر
        money_text = sa.report_manager_money(st)
        for mid in manager_ids:
            send_html(mid, money_text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
