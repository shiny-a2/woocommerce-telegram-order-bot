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
import crm
import db
import pipeline
import reports
import telegram_io
import woo

# ساعت کاری تهران برای ارسال لحظه‌ایِ لیدها (۱۰ تا قبل از ۱۹)
_BIZ_START, _BIZ_END = 10, 19
_RT_WINDOW = datetime.timedelta(hours=12)  # فقط ناموفق/لغوِ تازه (نه بک‌لاگِ قدیمی هنگام ری‌استارت)
_DUE_WINDOW = datetime.timedelta(days=14)  # یادآوری فقط برای پیگیری‌های اخیر، نه انبارِ قدیمی


def _recent(date_created):
    if not date_created:
        return True
    try:
        dt = datetime.datetime.fromisoformat(date_created)
        if dt.tzinfo is not None:  # اگر منطقه‌ی زمانی داشت، برهنه‌اش کن تا تفریق نشکند
            dt = dt.replace(tzinfo=None)
        return (clock.tehran_now() - dt) <= _RT_WINDOW
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
    if not (0 <= now.hour < 10):  # پنجره‌ی بعدِ نیمه‌شب تا پیشِ شیفت (مقاوم به ری‌استارت)
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
    """شروعِ شیفت (پنجره‌ی ۱۰ تا ۱۹): ناموفق/لغوی‌های شب را به گروه پیگیری بفرست."""
    now = clock.tehran_now()
    if not (10 <= now.hour < _BIZ_END):  # فقط در شیفت (سکوتِ بیرونِ شیفت حفظ می‌شود)
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


async def _maybe_shift_summary(app):
    """راس ساعت ۱۹ تهران (پایانِ شیفت): جمع‌بندیِ فعالیتِ اپراتورها به گروهِ پیگیری."""
    now = clock.tehran_now()
    if now.hour < _BIZ_END:  # از ۱۹ به بعد تا نیمه‌شب (مقاوم به ری‌استارت)
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_shift") == today:
        return
    group = telegram_io._followup_group()
    if not group:
        db.set_meta("last_shift", today)  # بدونِ گروه هم علامت بزن تا هر دقیقه تلاش نشود
        return
    try:
        await app.bot.send_message(group, text=telegram_io._shift_summary_text(), parse_mode="HTML")
        db.set_meta("last_shift", today)
        print("[shift] جمع‌بندیِ پایانِ شیفت ارسال شد.")
    except Exception as e:
        print(f"[shift] ارسالِ جمع‌بندی ناموفق بود: {e}")


async def _maybe_due_reminders(app):
    """در شیفت (۱۰ تا ۱۹): یادآوریِ پیگیری‌های سررسیدشده‌ی اخیر را به گروه بفرست.

    فیلترِ تازگی (۱۴ روز) انبارِ قدیمی را خارج می‌کند؛ ضدتکرار با due_sent؛ سقفِ هر دور.
    صبحِ شروعِ شیفت همه‌ی سررسیدهای شب و سرِ‌تایم هر یادآوری همان موقع می‌آید.
    """
    now = clock.tehran_now()
    if not (_BIZ_START <= now.hour < _BIZ_END):  # سکوتِ بیرونِ شیفت
        return
    if not telegram_io._followup_group() or not crm.enabled():
        return
    group = telegram_io._followup_group()
    after = (now - _DUE_WINDOW).strftime("%Y-%m-%d %H:%M")  # مرزِ پایین (تهران) — سرور یا پولر فیلتر می‌کند
    try:
        due = await crm.due_leads(after=after, limit=100)
    except Exception as e:
        print(f"[due] دریافتِ سررسیدها ناموفق بود: {e}")
        return
    floor = clock.utcnow() - _DUE_WINDOW
    sent = 0
    bad = 0
    for d in due:
        phone = d.get("phone")
        gmt = d.get("next_follow_up_gmt") or ""
        if not phone:
            continue
        try:  # فقط سررسیدهای اخیر (نه انبارِ قدیمیِ ۱۴۰۴)
            dt = datetime.datetime.fromisoformat(gmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            if dt < floor:
                continue
        except Exception:
            bad += 1
            continue
        key = f"{phone}|{gmt}"
        if db.due_sent(key):
            continue
        try:
            await app.bot.send_message(group, text=telegram_io._due_text(d), parse_mode="HTML",
                                       reply_markup=telegram_io._due_kb(phone))
            db.mark_due_sent(key)
            sent += 1
        except Exception as e:
            print(f"[due] ارسالِ یادآوریِ {phone}: {e}")
        await asyncio.sleep(0.3)
        if sent >= 15:
            print("[due] سقفِ ۱۵ یادآوری در این دور؛ بقیه دورِ بعد.")
            break
    if sent:
        print(f"[due] {sent} یادآوریِ پیگیری ارسال شد.")
    elif due and bad == len(due):
        print(f"[due] {len(due)} سررسید آمد ولی همه فرمتِ تاریخِ نامعتبر داشتند (next_follow_up_gmt؟).")


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
        try:  # هیچ خطایی نباید این تنها تسکِ پس‌زمینه را بی‌صدا بکُشد
            if cycle % 120 == 0:  # هر ~۲ ساعت ساعت را با منبع بیرونی همگام کن
                await clock.refresh()
            await _poll_orders(app)
            await _poll_edits(app)
            await _maybe_daily(app)
            await _maybe_leads(app)
            await _maybe_shift_summary(app)
            if cycle % 5 == 0:  # یادآوری‌ها هر ~۵ دقیقه (نه هر دقیقه)
                await _maybe_due_reminders(app)
            await reports.prewarm()  # کش را گرم نگه دار → گزارش‌های ادمین آنی
        except Exception as e:
            print(f"[poller] خطای سیکل: {e!r}")
        cycle += 1
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
