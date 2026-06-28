"""پولینگ پس‌زمینه:
- درج سفارش‌های موفقِ جدید
- بازسازی و ویرایش کپشنِ سفارش‌های اخیر در صورت تغییر (وضعیت/اصلاح قیمت/تعویض/الباقی)
"""
from __future__ import annotations

import asyncio
import datetime
import time

import clock
import config
import db
import pipeline
import reports
import telegram_io
import woo

# ساعت کاری تهران برای ارسال لحظه‌ایِ لیدها (۱۰ تا قبل از ۱۹)
_BIZ_START, _BIZ_END = 10, 19
_RT_WINDOW = datetime.timedelta(hours=12)  # فقط ناموفق/لغوِ تازه (نه بک‌لاگِ قدیمی هنگام ری‌استارت)


def _recent(date_created):
    if not date_created:
        return True
    try:
        return (clock.tehran_now() - datetime.datetime.fromisoformat(date_created)) <= _RT_WINDOW
    except Exception:
        return True


async def _push_one_lead(app, oid):
    """یک سفارش ناموفق/لغو را همان لحظه با دکمه‌ها به گروه پیگیری می‌فرستد."""
    if not telegram_io._followup_group():
        return
    try:
        o = await woo.get(f"orders/{oid}", {"_fields": "id,number,total,status,billing,line_items,date_created"})
    except Exception as e:
        print(f"[leads] گرفتن سفارش {oid}: {e}")
        return
    try:
        phone = (o.get("billing") or {}).get("phone")
        await app.bot.send_message(
            telegram_io._followup_group(), text=reports.lead_text(o), reply_markup=telegram_io._lead_kb(oid, phone)
        )
        db.mark_lead(oid)
        print(f"[leads] لیدِ {o.get('status')} #{oid} لحظه‌ای ارسال شد.")
    except Exception as e:
        print(f"[leads] ارسال لیدِ {oid}: {e}")


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
    biz = _BIZ_START <= clock.tehran_now().hour < _BIZ_END
    for o in reversed(orders):  # قدیمی‌تر اول
        oid = o.get("id")
        if not oid or oid <= baseline:  # سفارش‌های قدیمی‌تر از خط مبنا هرگز پست نمی‌شوند
            continue
        status = o.get("status")
        if status in config.POST_STATUSES and not db.is_posted(oid):  # پیش‌فیلتر → بدون فچِ الکی
            try:
                await pipeline.process_order(app, oid)
            except Exception as e:
                print(f"[poller] پردازش سفارش {oid} شکست خورد: {e}")
        # لیدِ لحظه‌ای: ناموفق/لغو در ساعت کاری → گروه پیگیری
        if biz and status in ("failed", "cancelled") and _recent(o.get("date_created")) and not db.lead_sent(oid):
            await _push_one_lead(app, oid)


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
        await reports.prewarm()  # کش را گرم نگه دار → گزارش‌های ادمین آنی
        cycle += 1
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
