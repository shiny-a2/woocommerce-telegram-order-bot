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
import recovery
import reports
import telegram_io
import wc_sync
import woo

# ساعت کاری تهران برای ارسال لحظه‌ایِ لیدها (۱۰ تا قبل از ۱۹)
_BIZ_START, _BIZ_END = 10, 19
_RT_WINDOW = datetime.timedelta(hours=12)  # فقط ناموفق/لغوِ تازه (نه بک‌لاگِ قدیمی هنگام ری‌استارت)
_DUE_WINDOW = datetime.timedelta(days=14)  # یادآوری فقط برای پیگیری‌های اخیر، نه انبارِ قدیمی
_THU_END = 14  # پنجشنبه شیفت تا ۱۴؛ جمعه تعطیل؛ شنبه–چهارشنبه تا _BIZ_END


def _shift_end_hour(now):
    """ساعتِ پایانِ شیفتِ امروز (تهران)؛ None یعنی امروز تعطیل است (جمعه)."""
    wd = (now.weekday() + 2) % 7  # شنبه=۰، یکشنبه=۱، … پنجشنبه=۵، جمعه=۶
    if wd == 6:            # جمعه
        return None
    if wd == 5:            # پنجشنبه
        return _THU_END
    return _BIZ_END        # شنبه تا چهارشنبه


def _in_shift(now=None) -> bool:
    """داخلِ شیفتِ کاری؟ شنبه–چهارشنبه ۱۰–۱۹، پنجشنبه ۱۰–۱۴، جمعه تعطیل."""
    now = now or clock.tehran_now()
    end = _shift_end_hour(now)
    return end is not None and _BIZ_START <= now.hour < end


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
        await telegram_io.send_to_managers(app, await reports.daily_summary_text())
        db.set_meta("last_daily", today)
        print("[daily] خلاصه‌ی فروش دیروز به مدیران ارسال شد.")
    except Exception as e:
        print(f"[daily] ارسال خلاصه ناموفق بود: {e!r}")


async def _maybe_leads(app):
    """شروعِ شیفت (پنجره‌ی ۱۰ تا ۱۹): ناموفق/لغوی‌های شب را به گروه پیگیری بفرست."""
    now = clock.tehran_now()
    if not _in_shift(now):  # فقط در شیفت (سکوتِ بیرونِ شیفت حفظ می‌شود)
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
    end = _shift_end_hour(now)
    if end is None or now.hour < end:  # روزِ تعطیل یا هنوز پیش از پایانِ شیفت
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_shift") == today:
        return
    try:  # عملکردِ اپراتورها فقط برای مدیران (نه گروهِ تیم)
        await telegram_io.send_to_managers(app, telegram_io._shift_summary_text(), parse_mode="HTML")
        db.set_meta("last_shift", today)
        print("[shift] جمع‌بندیِ پایانِ شیفت به مدیران ارسال شد.")
    except Exception as e:
        print(f"[shift] ارسالِ جمع‌بندی ناموفق بود: {e!r}")


def _recent_due(due):
    """فقط سررسیدهای اخیر (۱۴ روز) را با کلیدِ ضدتکرار برمی‌گرداند: [(d, key), …]."""
    floor = clock.utcnow() - _DUE_WINDOW
    out = []
    for d in due:
        phone = d.get("phone")
        gmt = d.get("next_follow_up_gmt") or ""
        if not phone:
            continue
        try:
            dt = datetime.datetime.fromisoformat(gmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            if dt < floor:
                continue
        except Exception:
            continue
        out.append((d, f"{phone}|{gmt}"))
    return out


async def _maybe_morning_worklist(app):
    """شروعِ شیفت (پنجره‌ی ۱۰–۱۹، یک‌بار در روز): «کارِ امروز» به تفکیکِ همکار به گروه.

    موارد را علامتِ ارسال می‌زند تا به‌صورتِ یادآوریِ تکی دوباره نیایند (فقط سررسیدهای
    جدیدِ حینِ روز به‌صورتِ تکی می‌آیند).
    """
    now = clock.tehran_now()
    if not _in_shift(now):
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_worklist") == today:
        return
    if not telegram_io._followup_group() or not crm.enabled():
        return
    group = telegram_io._followup_group()
    after = (now - _DUE_WINDOW).strftime("%Y-%m-%d %H:%M")
    before = now.strftime("%Y-%m-%d %H:%M")
    try:
        due = await crm.due_leads(after=after, before=before, limit=100)
    except Exception as e:
        print(f"[worklist] دریافت ناموفق: {e}")
        return
    db.set_meta("last_worklist", today)  # حتی اگر خالی، علامت بزن تا هر دقیقه تلاش نشود
    recent = _recent_due(due)
    if not recent:
        return
    groups = {}
    for d, _key in recent:
        groups.setdefault(d.get("assigned_name") or "بدونِ مسئول", []).append(d)
    try:
        await app.bot.send_message(group, text=telegram_io._worklist_text(groups), parse_mode="HTML")
        for _d, key in recent:  # تا یادآوریِ تکیِ همین‌ها دوباره نیاید
            db.mark_due_sent(key)
        print(f"[worklist] کارِ امروز ({len(recent)} پیگیری) ارسال شد.")
    except Exception as e:
        print(f"[worklist] ارسال ناموفق: {e}")


async def _maybe_due_reminders(app):
    """در شیفت (۱۰ تا ۱۹): یادآوریِ پیگیری‌های سررسیدشده‌ی اخیر را به گروه بفرست.

    فیلترِ تازگی (۱۴ روز) انبارِ قدیمی را خارج می‌کند؛ ضدتکرار با due_sent؛ سقفِ هر دور.
    صبحِ شروعِ شیفت همه‌ی سررسیدهای شب و سرِ‌تایم هر یادآوری همان موقع می‌آید.
    """
    now = clock.tehran_now()
    if not _in_shift(now):  # سکوتِ بیرونِ شیفت
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
    recent = _recent_due(due)
    if due and not recent:
        print(f"[due] {len(due)} سررسید آمد ولی هیچ‌کدام در پنجره‌ی اخیر/قابلِ‌پارس نبود.")
    sent = 0
    for d, key in recent:
        if db.due_sent(key):
            continue
        try:
            await app.bot.send_message(group, text=telegram_io._due_text(d), parse_mode="HTML",
                                       reply_markup=telegram_io._due_kb(d.get("phone")))
            db.mark_due_sent(key)
            sent += 1
        except Exception as e:
            print(f"[due] ارسالِ یادآوریِ {d.get('phone')}: {e}")
        await asyncio.sleep(0.3)
        if sent >= 15:
            print("[due] سقفِ ۱۵ یادآوری در این دور؛ بقیه دورِ بعد.")
            break
    if sent:
        print(f"[due] {sent} یادآوریِ پیگیری ارسال شد.")


async def _poll_new_leads(app):
    """کارتِ خودکارِ لیدِ جدید در گروه (فقط در شیفت؛ شب‌ها صبح می‌آید).

    تا `crm_newlead_on=1` ست نشود خاموش است. خط مبنا (`crm_lead_baseline`) از بک‌فیلِ
    لیدهای قدیمی جلوگیری می‌کند. baseline فقط تا لیدی که واقعاً ارسال شد جلو می‌رود.
    """
    if db.get_meta("crm_newlead_on") != "1":
        return
    now = clock.tehran_now()
    if not _in_shift(now):
        return
    if not telegram_io._followup_group() or not crm.enabled():
        return
    baseline = db.get_meta("crm_lead_baseline")
    try:
        if baseline is None:  # اولین بار: خط مبنا = ماکزیمم فعلی، بدونِ ارسالِ بک‌لاگ
            data = await crm.new_leads(since_id=2_000_000_000, limit=1)
            db.set_meta("crm_lead_baseline", int(data.get("max_id") or 0))
            print(f"[newlead] خط مبنا روی {int(data.get('max_id') or 0)} تنظیم شد.")
            return
        res = await crm.new_leads(since_id=int(baseline), limit=50)
    except Exception as e:
        print(f"[newlead] دریافت ناموفق: {e}")
        return
    leads = res.get("leads") or []
    group = telegram_io._followup_group()
    last_id = int(baseline)
    sent = 0
    for L in leads:  # مرتب بر اساس id صعودی
        if not L.get("phone"):
            last_id = max(last_id, int(L.get("id") or last_id))
            continue
        try:
            await app.bot.send_message(group, text=telegram_io._newlead_text(L), parse_mode="HTML",
                                       reply_markup=telegram_io._newlead_kb(L.get("phone")))
            last_id = int(L.get("id") or last_id)
            sent += 1
        except Exception as e:
            print(f"[newlead] ارسالِ {L.get('phone')}: {e}")
            break  # baseline را جلو نبر تا دورِ بعد دوباره تلاش شود
        await asyncio.sleep(0.4)
        if sent >= 20:
            break
    if last_id > int(baseline):
        db.set_meta("crm_lead_baseline", last_id)
    if sent:
        print(f"[newlead] {sent} لیدِ جدید به گروه ارسال شد.")


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
    if config.WC_INCREMENTAL:
        await _poll_edits_incremental(app)
    else:
        await _poll_edits_full(app)


async def _poll_edits_full(app):
    """مسیرِ قدیمی (full-scan) — فقط با WC_INCREMENTAL=off؛ به‌عنوانِ fallback نگه داشته شده."""
    since = time.time() - config.NOTE_LOOKBACK_DAYS * 86400
    for oid in db.tracked_orders(since):
        try:
            await pipeline.rebuild_and_edit(app, oid)
        except Exception as e:
            print(f"[poller] بازبینی سفارش {oid} شکست خورد: {e}")


async def _maybe_reconcile(app):
    """آشتیِ کامل روزی یک‌بار در ساعتِ کم‌ترافیک (۳–۵ صبح): full-scan برای گرفتنِ هر تغییرِ جاافتاده (تورِ ایمنی)."""
    now = clock.tehran_now()
    if not (3 <= now.hour < 5):
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_reconcile") == today:
        return
    db.set_meta("last_reconcile", today)
    print("[wc] آشتیِ روزانه (full-scan) — ساعتِ کم‌ترافیک…")
    try:
        await _poll_edits_full(app)  # با rate-limiter محافظت می‌شود
    except Exception as e:
        print(f"[wc] آشتیِ روزانه خطا: {e!r}")
    print("[wc] آشتیِ روزانه تمام شد.")


async def _poll_edits_incremental(app):
    """فقط سفارش‌هایی که date_modified‌شان عوض شده یا خیلی تازه‌اند rebuild می‌شوند (نه فچِ همه)."""
    since = time.time() - config.NOTE_LOOKBACK_DAYS * 86400
    tracked = db.tracked_orders(since)
    if not tracked:
        return
    changed = await wc_sync.changed_since_last()  # {oid: date_modified} یا None اگر sync ناموفق
    if changed is None:
        return  # سایت در دسترس نبود → این دور رد کن، دورِ بعد دوباره
    stored = db.orders_modified_map()
    fresh = set(db.tracked_orders(time.time() - config.WC_EDIT_FRESH_HOURS * 3600))  # تازه‌ها → نوت‌گیری
    edited = 0
    for oid in tracked:
        dm = changed.get(oid)
        if not ((oid in fresh) or (dm is not None and dm != stored.get(oid))):
            continue  # نه تغییر کرده نه تازه → فچِ detail نکن
        try:
            await pipeline.rebuild_and_edit(app, oid)
            if dm:
                db.set_order_modified(oid, dm)
            edited += 1
        except Exception as e:
            print(f"[poller] بازبینی سفارش {oid} شکست خورد: {e}")
    if edited:
        print(f"[wc] {edited} سفارش بازبینی شد (از {len(tracked)} ردیابی‌شده) — بدونِ full-scan.")


async def run(app):
    print(f"[poller] شروع شد، هر {config.POLL_INTERVAL_SECONDS} ثانیه.")
    cycle = 0
    while True:
        try:  # هیچ خطایی نباید این تنها تسکِ پس‌زمینه را بی‌صدا بکُشد
            if cycle % 120 == 0:  # هر ~۲ ساعت ساعت را با منبع بیرونی همگام کن
                await clock.refresh()
            await _poll_orders(app)
            await _poll_new_leads(app)
            await _poll_edits(app)
            await _maybe_daily(app)
            await _maybe_reconcile(app)  # آشتیِ کامل روزی یک‌بار (۳–۵ صبح)
            await _maybe_leads(app)
            await _maybe_shift_summary(app)
            await _maybe_morning_worklist(app)  # «کارِ امروز» سرِ شیفت (و علامتِ ارسال)
            if cycle % 5 == 0:  # هر ~۵ دقیقه
                await _maybe_due_reminders(app)
            try:
                await recovery.tick(app)  # هر چرخه (~۱ دقیقه) → شلیکِ به‌موقعِ بازیابی، بدونِ لگ
            except Exception as e:
                print(f"[recover] tick خطا: {e!r}")
            await reports.prewarm()  # کش را گرم نگه دار → گزارش‌های ادمین آنی
        except Exception as e:
            print(f"[poller] خطای سیکل: {e!r}")
        if cycle % 10 == 0:  # هر ~۱۰ دقیقه: نرخِ درخواستِ ووکامرس (برای رصدِ فشار روی سایت)
            _r = woo.req_count()
            _p = int(db.get_meta("wc_req_mark") or _r)
            print(f"[wc] ~{_r - _p} درخواستِ ووکامرس در ~۱۰ دقیقه ({(_r - _p) / 10:.1f}/دقیقه)")
            db.set_meta("wc_req_mark", _r)
        cycle += 1
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
