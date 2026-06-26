"""پولینگ پس‌زمینه:
- درج سفارش‌های موفقِ جدید
- بازسازی و ویرایش کپشنِ سفارش‌های اخیر در صورت تغییر (وضعیت/اصلاح قیمت/تعویض/الباقی)
"""
from __future__ import annotations

import asyncio
import time

import config
import db
import pipeline
import woo


async def _poll_orders(app):
    baseline = int(db.get_meta("baseline_id") or 0)
    try:
        orders = await woo.list_recent_orders(per_page=100)
    except Exception as e:
        print(f"[poller] گرفتن سفارش‌ها شکست خورد: {e}")
        return
    for o in reversed(orders):  # قدیمی‌تر اول
        oid = o.get("id")
        if not oid or oid <= baseline:  # سفارش‌های قدیمی‌تر از خط مبنا هرگز پست نمی‌شوند
            continue
        if not db.is_posted(oid):
            try:
                await pipeline.process_order(app, oid)
            except Exception as e:
                print(f"[poller] پردازش سفارش {oid} شکست خورد: {e}")


async def _poll_edits(app):
    since = time.time() - config.NOTE_LOOKBACK_DAYS * 86400
    for oid in db.tracked_orders(since):
        try:
            await pipeline.rebuild_and_edit(app, oid)
        except Exception as e:
            print(f"[poller] بازبینی سفارش {oid} شکست خورد: {e}")


async def run(app):
    print(f"[poller] شروع شد، هر {config.POLL_INTERVAL_SECONDS} ثانیه.")
    while True:
        await _poll_orders(app)
        await _poll_edits(app)
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
