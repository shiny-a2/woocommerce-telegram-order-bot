"""Sync افزایشیِ سفارش‌ها: فقط سفارش‌های تغییرکرده را (با overlap امن) از ووکامرس می‌گیرد و لاگ می‌کند.

به‌جای فچِ detailِ همه‌ی سفارش‌های اخیر در هر دور، یک لیستِ سبک با modified_after می‌گیرد
و فقط سفارش‌هایی که date_modified‌شان عوض شده (یا خیلی تازه‌اند) دوباره rebuild می‌شوند.
"""
from __future__ import annotations

import datetime
import time

import clock
import config
import db
import woo


async def changed_since_last():
    """{order_id: date_modified_gmt} برای سفارش‌های modified از آخرین sync موفق.

    از (last_sync − overlap) می‌خواند تا هیچ سفارشی جا نیفتد. last_sync را فقط در صورتِ موفقیت
    جلو می‌برد (idempotent). خروجی None یعنی sync ناموفق (سایت در دسترس نبود).
    """
    now = clock.utcnow()
    last = db.get_meta("last_wc_sync_at")
    try:
        base = datetime.datetime.fromisoformat(last) if last else None
    except Exception:
        base = None
    if base is None:
        after = now - datetime.timedelta(hours=config.WC_SYNC_BACKFILL_H)
    else:
        after = base - datetime.timedelta(minutes=config.WC_OVERLAP_MIN)
    after_iso = after.strftime("%Y-%m-%dT%H:%M:%S")

    t0, req0 = time.time(), woo.req_count()
    try:
        orders, pages = await woo.list_modified_orders(after_iso)
    except Exception as e:
        db.log_wc_sync("orders", 0, 0, woo.req_count() - req0, time.time() - t0, str(e)[:200])
        return None  # سایت در دسترس نبود → last_sync را جلو نبر تا دورِ بعد دوباره تلاش شود

    m = {o.get("id"): o.get("date_modified_gmt") for o in orders if o.get("id")}
    db.set_meta("last_wc_sync_at", now.replace(microsecond=0).isoformat())
    db.log_wc_sync("orders", pages, len(orders), woo.req_count() - req0, time.time() - t0, "")
    return m
