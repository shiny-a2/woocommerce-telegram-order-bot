"""پولینگ پس‌زمینه:
- درج سفارش‌های موفقِ جدید
- بازسازی و ویرایش کپشنِ سفارش‌های اخیر در صورت تغییر (وضعیت/اصلاح قیمت/تعویض/الباقی)
"""
from __future__ import annotations

import asyncio
import time

import clock
import config
import db
import pipeline
import reports
import telegram_io
import woo


async def _maybe_daily(app):
    now = clock.tehran_now()
    if now.hour != 0:  # فقط راس نیمه‌شب تهران (۰۰:۰۰–۰۰:۵۹)
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_daily") == today:  # امروز قبلاً فرستاده شده
        return
    try:
        await app.bot.send_message(
            chat_id=config.TELEGRAM_GROUP_ID, text=await reports.daily_summary_text()
        )
        db.set_meta("last_daily", today)
        print("[daily] خلاصه‌ی فروش دیروز ارسال شد.")
    except Exception as e:
        print(f"[daily] ارسال خلاصه ناموفق بود: {e}")


async def _maybe_leads(app):
    """راس ۱۰ صبح تهران: ناموفق + لغوی‌های ۲۴ ساعت اخیر را به گروه پیگیری بفرست."""
    now = clock.tehran_now()
    if now.hour != 10:
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_leads") == today:
        return
    try:
        res = await telegram_io.push_leads(app, 1, ("failed", "cancelled"))
        if res is not None:
            db.set_meta("last_leads", today)
            print(f"[leads] {res[0]} لیدِ ناموفق/لغوِ ۲۴ ساعت اخیر به گروه پیگیری ارسال شد.")
    except Exception as e:
        print(f"[leads] ارسال لیدها ناموفق بود: {e}")


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
    cycle = 0
    while True:
        if cycle % 120 == 0:  # هر ~۲ ساعت ساعت را با منبع بیرونی همگام کن
            await clock.refresh()
        await _poll_orders(app)
        await _poll_edits(app)
        await _maybe_daily(app)
        await _maybe_leads(app)
        cycle += 1
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
