"""سمت تلگرام: ارسال سفارش، ویرایش کپشن، منوی دکمه‌ای گزارش‌ها و جستجوی سفارش."""
from __future__ import annotations

import asyncio
import datetime
import html
import io
import re
import time

import jdatetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import clock
import config
import crm
import crm_view
import db
import igstats
import reports
import woo
import worktasks

# نام فارسی وضعیت‌ها (شامل وضعیت‌های سفارشی فروشگاه مثل deliver)
_STATUS_FA = {
    "pending": "در انتظار پرداخت",
    "processing": "در حال انجام",
    "on-hold": "در انتظار بررسی",
    "completed": "تکمیل شده",
    "cancelled": "لغو شده",
    "refunded": "مرجوع شده",
    "failed": "ناموفق",
    "deliver": "تحویل شده",
    "delivered": "تحویل شده",
    "trash": "حذف شده",
}
_STATUS_EMOJI = {
    "cancelled": "❌", "refunded": "↩️", "failed": "⚠️",
    "completed": "✅", "deliver": "✅", "delivered": "✅",
    "processing": "🛠️", "on-hold": "⏸️", "pending": "🕒",
}


def _esc(x):
    return html.escape(str(x or ""))


def _status_line(order) -> str:
    s = order.get("status") or ""
    return f"{_STATUS_EMOJI.get(s, '🔖')} وضعیت: {_esc(_STATUS_FA.get(s, s))}"


def _product_line(order) -> str:
    items = order.get("line_items") or []
    parts = []
    for it in items:
        nm = _esc(it.get("name", ""))
        q = it.get("quantity", 1)
        parts.append(f"{nm} ×{q}" if q and q != 1 else nm)
    if not parts:
        return "🛍️ محصول: —"
    if len(parts) == 1:
        return f"🛍️ محصول: {parts[0]}"
    return "🛍️ محصول:\n" + "\n".join("• " + p for p in parts)


def build_caption(order, stock_location=None, summary=None) -> str:
    f = woo.caption_fields(order)
    summary = summary or {}
    location = summary.get("location") or stock_location  # موقعیت دقیقِ پلاگین مقدم است
    corrections = summary.get("corrections")
    operations = summary.get("operations")

    jdate = reports.jalali_str(f["date_created"]) if f["date_created"] else "—"
    lines = [
        f"🧾 شماره سفارش: <b>{_esc(f['number'])}</b>",
        f"📅 تاریخ سفارش: {_esc(jdate)}",
        _status_line(order),
        "",
        f"💳 روش پرداخت: {_esc(f['payment'])}",
        f"🚚 روش حمل: {_esc(f['shipping'])}",
        f"👤 خریدار: {_esc(f['name'])}",
        f"📞 تماس: {_esc(f['phone'])}",
        f"📍 استان: {_esc(f['province'])}",
        f"🏠 آدرس: {_esc(f['address'])}",
    ]
    if f["postcode"]:
        lines.append(f"📮 کدپستی: {_esc(f['postcode'])}")
    lines.append("")
    lines.append(_product_line(order))
    if location:
        lines.append(f"📦 موقعیت موجودی: {_esc(location)}")
    # تفکیکِ مالی: اگر تخفیف دارد، قیمتِ قبل تخفیف + مبلغِ تخفیف/کوپن + حملِ اگر بود + پرداختی
    try:
        disc = float(f.get("discount_total") or 0)
        ship = float(f.get("shipping_total") or 0)
    except (TypeError, ValueError):
        disc, ship = 0.0, 0.0
    cl_ = config.CURRENCY_LABEL
    if disc > 0:
        lines.append(f"🏷️ قیمت قبل تخفیف: {reports.fmt_money(f.get('items_subtotal') or 0)} {cl_}")
        cps = [c for c in (f.get('coupons') or []) if c]
        cp_txt = (" (کوپن: " + "، ".join(_esc(c) for c in cps) + ")") if cps else ""
        lines.append(f"➖ تخفیف{cp_txt}: {reports.fmt_money(disc)} {cl_}")
        if ship > 0:
            lines.append(f"🚚 هزینه ارسال: {reports.fmt_money(ship)} {cl_}")
        lines.append(f"💰 مبلغ پرداختی: {reports.fmt_money(f['total'])} {cl_}")
    elif not summary.get("has_payment"):
        lines.append(f"💰 مبلغ پرداختی: {reports.fmt_money(f['total'])} {cl_}")
    if corrections:
        lines.append("")
        lines.append("➖ اصلاحات سفارش:")
        lines.extend(corrections)
    if operations:
        lines.append("")
        lines.append("📋 ثبت عملیات سفارش:")
        lines.extend(operations)
    return "\n".join(lines)


async def send_card(app: Application, chat_id, photos, caption) -> int:
    """یک کارت سفارش (آلبوم عکس + کپشن) را به چت داده‌شده می‌فرستد و آیدی پیام اول را برمی‌گرداند."""
    if photos:
        items = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=ParseMode.HTML)]
        items += [InputMediaPhoto(media=d) for d in photos[1:]]
        msgs = await app.bot.send_media_group(chat_id=chat_id, media=items)
        return msgs[0].message_id
    msg = await app.bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML)
    return msg.message_id


async def post_order(app: Application, order, photos, caption) -> int:
    return await send_card(app, config.TELEGRAM_GROUP_ID, photos, caption)


async def edit_caption(app: Application, message_id, chat_id, caption):
    await app.bot.edit_message_caption(
        chat_id=chat_id, message_id=message_id, caption=caption, parse_mode=ParseMode.HTML
    )


async def edit_media_photo(app: Application, message_id, chat_id, photo, caption):
    """عکسِ شاخصِ یک کارتِ سفارش را (مثلاً پس از تعویضِ ساعت) روی همان پیام جایگزین می‌کند."""
    await app.bot.edit_message_media(
        chat_id=chat_id, message_id=message_id,
        media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
    )


# ---------- منو، گزارش‌ها و جستجو (فقط اعضای مجاز) ----------

_MENU_TITLE = "🛍️ <b>منوی مدیریت فروش</b>\nیک گزینه را انتخاب کنید:"


def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in config.ADMIN_USER_IDS


def _main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 خلاصه‌ی مدیریتی (این ماه)", callback_data="rep:overview")],
        [InlineKeyboardButton("📊 فروش امروز", callback_data="rep:today"),
         InlineKeyboardButton("📅 این هفته", callback_data="rep:week")],
        [InlineKeyboardButton("🗓️ این ماه", callback_data="rep:month"),
         InlineKeyboardButton("📈 کل امسال", callback_data="rep:year")],
        [InlineKeyboardButton("📆 انتخاب ماه (به تفکیک درگاه)", callback_data="menu:months")],
        [InlineKeyboardButton("📈 آمار و تحلیل", callback_data="menu:analytics"),
         InlineKeyboardButton("📦 در انتظار ارسال", callback_data="rep:pending")],
        [InlineKeyboardButton("📞 پیگیری رهاشده‌ها", callback_data="followup"),
         InlineKeyboardButton("📊 نتایج پیگیری", callback_data="outcomes")],
        [InlineKeyboardButton("📄 خروجی اکسل (این ماه)", callback_data="csv:month")],
        [InlineKeyboardButton("🔍 جستجوی سفارش", callback_data="search")],
    ])


def _analytics_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 روند ۶ ماه اخیر", callback_data="rep:trend"),
         InlineKeyboardButton("🏦 عملکرد درگاه‌ها", callback_data="rep:gwperf")],
        [InlineKeyboardButton("🏆 پرفروش‌ترین محصولات", callback_data="rep:topproducts"),
         InlineKeyboardButton("👤 بهترین مشتری‌ها", callback_data="rep:customers")],
        [InlineKeyboardButton("📊 مقایسه با ماه قبل", callback_data="rep:compare"),
         InlineKeyboardButton("🧮 آمار کلی", callback_data="rep:stats")],
        [InlineKeyboardButton("🗺️ تفکیک استان", callback_data="rep:province")],
        [InlineKeyboardButton("🔙 منو", callback_data="menu:main")],
    ])


def _months_menu(jy):
    rows, row = [], []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(reports.J_MONTHS[m - 1], callback_data=f"jm:{jy}:{m}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = [InlineKeyboardButton(f"◀ {jy - 1}", callback_data=f"months:{jy - 1}")]
    if jy < reports.current_jyear():
        nav.append(InlineKeyboardButton(f"{jy + 1} ▶", callback_data=f"months:{jy + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="menu:main")]])


# ---------- پیگیری رهاشده‌ها (دکمه‌های زیر هر لید) ----------

_LEAD_ACTIONS = {"contacted": "📞 تماس شد", "noanswer": "🚫 پاسخ نداد", "bought": "✅ خرید کرد"}


def _tg_link(phone):
    p = re.sub(r"\D", "", phone or "")
    if not p:
        return None
    if p.startswith("00"):
        p = p[2:]
    elif p.startswith("0"):
        p = "98" + p[1:]
    return f"https://t.me/+{p}"


def _lead_kb(oid, phone=None):
    rows = [[
        InlineKeyboardButton("📞 تماس شد", callback_data=f"lead:contacted:{oid}"),
        InlineKeyboardButton("🔕 پاسخ نداد", callback_data=f"lead:noanswer:{oid}"),
        InlineKeyboardButton("✅ خرید کرد", callback_data=f"lead:bought:{oid}"),
    ]]
    link = _tg_link(phone)
    if link:
        rows.append([InlineKeyboardButton("💬 پیام در تلگرام", url=link)])
    p = crm.normalize_phone(phone or "")
    if p and crm.enabled():
        rows.append([crm_view.open_button(p)])
    rows.append([InlineKeyboardButton("✖️ بستن", callback_data="crm:close")])
    return InlineKeyboardMarkup(rows)


def _followup_group():
    return int(db.get_meta("followup_group") or config.FOLLOWUP_GROUP_ID or 0)


async def send_to_managers(app, text, parse_mode=None, reply_markup=None):
    """گزارش‌های مدیریتی فقط به مدیران: REPORTS_CHAT_ID، وگرنه پیویِ تک‌تکِ ادمین‌ها."""
    if config.REPORTS_CHAT_ID:
        try:
            await app.bot.send_message(config.REPORTS_CHAT_ID, text, parse_mode=parse_mode,
                                       reply_markup=reply_markup)
        except Exception as e:
            print(f"[managers] ارسال به REPORTS_CHAT_ID ناموفق: {e!r}")
        return
    for uid in config.ADMIN_USER_IDS:
        try:
            await app.bot.send_message(uid, text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            print(f"[managers] ارسال به {uid} ناموفق: {e!r}")


# ---------- CRM (تیمِ فروش: ادمین‌ها یا گروهِ پیگیری) ----------
def _crm_can_read(q) -> bool:
    """دسترسیِ CRM: ادمین‌ها همه‌جا؛ یا اعضای گروهِ پیگیری. (گروهِ اصلی نه.)"""
    if q.from_user and q.from_user.id in config.ADMIN_USER_IDS:
        return True
    chat_id = q.message.chat_id if q.message else 0
    return chat_id == _followup_group()


def _actor_name(user) -> str:
    """نامِ نمایشیِ اپراتورِ زننده برای ثبت در CRM."""
    if not user:
        return "اپراتور"
    return user.full_name or (("@" + user.username) if user.username else str(user.id))


async def _crm_card(phone):
    """(متنِ کارت، کیبورد) — پروفایل + بازدیدِ صفحاتِ مشتری همزمان؛ اگر رکورد پیدا شد کیبوردِ اقدام."""
    try:
        prof_r, viewed_r = await asyncio.gather(
            crm.get_profile(phone),
            crm.viewed_products(phone, limit=25),
            return_exceptions=True,
        )
        if isinstance(prof_r, Exception):
            raise prof_r
        viewed = viewed_r if isinstance(viewed_r, list) else []
        if not isinstance(viewed_r, list):  # واکشیِ بازدید نباید کارت را بشکند
            print(f"[crm] viewed(card) {phone}: {viewed_r!r}")
        text = crm_view.render_profile(prof_r, viewed=viewed)
        kb = crm_view.action_kb(phone) if prof_r.get("found") else crm_view.read_kb(phone)
    except Exception as e:
        print(f"[crm] دریافتِ پروفایلِ {phone}: {e!r}")
        text = "⚠️ خطا در دریافت اطلاعاتِ CRM. کمی بعد دوباره امتحان کن."
        kb = crm_view.read_kb(phone)
    return text, kb


async def _crm_prompt(context, chat_id, reply_to, text):
    try:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_to_message_id=reply_to)
    except Exception:
        pass


_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _parse_followup(text):
    """تاریخِ شمسی یا میلادیِ تایپ‌شده → «YYYY-MM-DD HH:MM» میلادی، یا None اگر نامعتبر."""
    t = (text or "").translate(_FA_DIGITS).strip()
    m = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})(?:\D+(\d{1,2})\D(\d{1,2}))?", t)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4)) if m.group(4) else 10
    mi = int(m.group(5)) if m.group(5) else 0
    try:
        if y < 1700:  # شمسی
            g = jdatetime.datetime(y, mo, d, hh, mi).togregorian()
        else:  # میلادی
            g = datetime.datetime(y, mo, d, hh, mi)
        return g.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _to_jalali(greg_str):
    """«YYYY-MM-DD HH:MM» میلادی → نمایشِ شمسی برای اپراتور."""
    try:
        g = datetime.datetime.strptime(greg_str, "%Y-%m-%d %H:%M")
        return jdatetime.datetime.fromgregorian(datetime=g).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return greg_str


async def _safe_answer(q, text="", show_alert=False):
    """پاسخِ کوئری که هرگز هندلر را نمی‌اندازد (کوئریِ قدیمی/تایم‌اوت را بی‌صدا رد می‌کند).

    اگر کوئری منقضی شده باشد، Telegram خطا می‌دهد؛ ولی نباید جلوِ ادامه‌ی کار (آپدیتِ کارت
    با edit_message که مستقل از عمرِ کوئری است) را بگیرد.
    """
    try:
        await q.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def _handle_crm(q, context):
    data = q.data or ""
    if data == "crm:close":
        await _safe_answer(q,)
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if not crm.enabled():
        await _safe_answer(q,"اتصال CRM فعال نیست.", show_alert=True)
        return
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    phone = crm.normalize_phone(parts[2]) if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    actor = _actor_name(q.from_user)
    uid = q.from_user.id if q.from_user else 0
    if q.message is None:  # کارتِ قدیمی‌تر از ~۴۸ ساعت → callback بدونِ message
        await _safe_answer(q,"این کارت قدیمی شده؛ دوباره /crm را بزن.", show_alert=True)
        return

    async def _refresh():
        if _is_newlead(q.message):  # کارتِ لیدِ جدید را فشرده و در جا با وضعیتِ به‌روز رندر کن
            text, kb = await _newlead_card_after(phone), _newlead_kb(phone)
        else:
            text, kb = await _crm_card(phone)
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                print(f"[crm] بروزرسانیِ کارت ناموفق: {e!r}")

    if action == "open":  # کارتِ تازه
        await _safe_answer(q,"در حال دریافت…")
        text, kb = await _crm_card(phone)
        await context.bot.send_message(q.message.chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    if action == "refresh":
        await _safe_answer(q,)
        await _refresh()
        return
    if action == "note":  # درخواستِ یادداشت با ریپلای
        await _safe_answer(q,)
        await _crm_prompt(context, q.message.chat_id, q.message.message_id,
                          f"📝 یادداشتت را در ریپلای به همین پیام بنویس.\n<code>{phone}</code>")
        return
    if action == "editname":  # ویرایشِ نام و نام‌خانوادگی با ریپلای
        await _safe_answer(q,)
        await _crm_prompt(context, q.message.chat_id, q.message.message_id,
                          f"✏️ نام و نام‌خانوادگی را بنویس و روی همین پیام ریپلای کن.\n<code>{phone}</code>")
        return
    if action == "orders":  # سفارش‌های مشتری از ووکامرس
        await _safe_answer(q,"در حال دریافت سفارش‌ها…")
        try:
            orders = await woo.search_orders(phone, per_page=10)
        except Exception as e:
            print(f"[crm] orders {phone}: {e!r}")
            await _safe_answer(q,"خطا در دریافت سفارش‌ها ❌", show_alert=True)
            return
        try:
            await q.edit_message_text(_orders_text(phone, orders), parse_mode=ParseMode.HTML,
                                      reply_markup=_back_only_kb(phone))
        except Exception as e:
            if "not modified" not in str(e).lower():
                print(f"[crm] orders edit: {e!r}")
        return
    if action == "viewed":  # محصولاتِ مشاهده‌شده از CRM
        await _safe_answer(q,"در حال دریافت…")
        try:
            body = _viewed_text(phone, await crm.viewed_products(phone))
        except Exception as e:
            print(f"[crm] viewed {phone}: {e!r}")
            body = "👁️ بخشِ «محصولاتِ دیده‌شده» هنوز سمتِ CRM فعال نیست."
        try:
            await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=_back_only_kb(phone))
        except Exception as e:
            if "not modified" not in str(e).lower():
                print(f"[crm] viewed edit: {e!r}")
        return
    if action == "recommend":  # پیشنهادِ محصول از موتورِ CRM
        await _safe_answer(q,"در حال دریافت پیشنهادها…")
        try:
            body = _recommend_text(phone, await crm.recommend(phone))
        except Exception as e:
            print(f"[crm] recommend {phone}: {e!r}")
            body = "🎯 بخشِ «پیشنهادِ محصول» هنوز سمتِ CRM فعال نیست."
        try:
            await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=_back_only_kb(phone))
        except Exception as e:
            if "not modified" not in str(e).lower():
                print(f"[crm] recommend edit: {e!r}")
        return
    if action == "fu":  # منوی تعیینِ پیگیری
        await _safe_answer(q,)
        try:
            await q.edit_message_reply_markup(reply_markup=crm_view.followup_kb(phone))
        except Exception:
            pass
        return
    if action == "fucustom":  # تاریخِ دلخواه با ریپلای
        await _safe_answer(q,)
        await _crm_prompt(context, q.message.chat_id, q.message.message_id,
                          f"🗓️ تاریخِ پیگیری را بنویس و روی همین پیام ریپلای کن (مثل: ۱۴۰۵/۰۵/۰۱ یا 2026-07-01 10:30).\n<code>{phone}</code>")
        return
    if action == "setfu":  # ثبتِ پیگیری از گزینه‌ی سریع
        try:
            days = int(arg)
        except ValueError:
            await _safe_answer(q,)
            return
        when = (clock.tehran_now() + datetime.timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
        dt = when.strftime("%Y-%m-%d %H:%M")
        await _safe_answer(q,f"⏳ ثبتِ پیگیری: {_to_jalali(dt)}…")  # پاسخِ فوری قبل از کارِ کندِ CRM
        try:  # هم وضعیت=پیگیری هم تاریخ → مطمئن در /due می‌آید
            await crm.set_status(phone, "follow_up", actor, follow_up_at=dt)
        except Exception as e:
            print(f"[crm] setfu {phone} {dt}: {e!r}")
            await _safe_answer(q,"خطا در ثبتِ پیگیری ❌", show_alert=True)
            return
        db.record_crm_action(phone, "followup", uid, actor, detail=dt)
        await _refresh()
        return
    if action == "mst":  # منوی تغییرِ وضعیت
        await _safe_answer(q,)
        try:
            await q.edit_message_reply_markup(reply_markup=crm_view.status_kb(phone))
        except Exception:
            pass
        return
    if action == "sst":  # ثبتِ وضعیت
        await _safe_answer(q,"⏳ در حال ثبت…")  # پاسخِ فوری قبل از کارِ کندِ CRM → «Query too old» نمی‌شود
        try:
            r = await crm.set_status(phone, arg, actor)
        except Exception as e:
            print(f"[crm] set_status {phone} {arg}: {e!r}")
            await _safe_answer(q,"خطا در ثبتِ وضعیت ❌", show_alert=True)
            return
        db.record_crm_action(phone, "status", uid, actor, detail=arg)
        await _refresh()
        if arg == "purchased_other_site":
            await _crm_prompt(context, q.message.chat_id, q.message.message_id,
                              f"⚠️ یک قدم مانده — 🌐 از کدام سایت خرید کرد؟ روی همین پیام ریپلای کن.\n<code>{phone}</code>")
        elif arg == "product_unavailable":
            await _crm_prompt(context, q.message.chat_id, q.message.message_id,
                              f"⚠️ یک قدم مانده — 📦 کدام محصول ناموجود بود؟ روی همین پیام ریپلای کن.\n<code>{phone}</code>")
        return
    if action == "masg":  # منوی اساین
        await _safe_answer(q,"بارگذاری همکاران…")
        try:
            agents = await crm.get_agents()
        except Exception as e:
            print(f"[crm] get_agents: {e!r}")
            await _safe_answer(q,"خطا در دریافتِ همکاران ❌", show_alert=True)
            return
        try:
            await q.edit_message_reply_markup(reply_markup=crm_view.assign_kb(phone, agents))
        except Exception:
            pass
        return
    if action == "sasg":  # ثبتِ اساین
        await _safe_answer(q,"⏳ در حال اساین…")  # پاسخِ فوری قبل از کارِ کندِ CRM
        try:
            r = await crm.assign(phone, int(arg), actor)
        except Exception as e:
            print(f"[crm] assign {phone} {arg}: {e!r}")
            await _safe_answer(q,"خطا در اساین ❌", show_alert=True)
            return
        db.record_crm_action(phone, "assign", uid, actor, detail=r.get("assigned_name", ""))
        await _refresh()
        return
    await _safe_answer(q,)


async def cmd_crm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """‎/crm 09xxxxxxxxx → کارتِ کاملِ مشتری/لید از CRM."""
    msg = update.effective_message
    if not msg:
        return
    chat = update.effective_chat
    print(f"[cmd] /crm از {update.effective_user.id if update.effective_user else '?'} در چت {chat.id if chat else '?'} args={context.args}")
    allowed = _authorized(update) or (chat and chat.id == _followup_group())
    if not allowed:
        return
    if not crm.enabled():
        await msg.reply_text("اتصال CRM فعال نیست.")
        return
    phone = crm.normalize_phone((context.args or [""])[0])
    if not phone:
        await msg.reply_text("شماره را بده؛ مثلاً:  /crm 09121234567")
        return
    wait = await msg.reply_text("⏳ در حال دریافت پروفایل CRM…")
    text, kb = await _crm_card(phone)
    await wait.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------- «/newleads»: بوردِ لیدهای «جدید»ِ ۳۰ روزِ اخیر در گروه (dedup) ----------
_NEWCARD_DELAY = 2.5           # ثانیه بین ارسال‌ها (رعایتِ سقفِ نرخِ گروهِ تلگرام ~۲۰/دقیقه)
_NEWCARD_CAP = 800            # سقفِ ایمنی برای جلوگیری از فلادِ ناخواسته
_POPUP_SOURCES = {"website_popup", "popup"}  # پاپ‌آپِ ناشناسِ سایت (کم‌تعامل) — پیش‌فرض حذف
_newcards_running: set = set()


async def _nl_retry(since_id, limit):
    """/new-leads با تحملِ dropِ گذرای سایت."""
    for a in range(3):
        try:
            return await crm.new_leads(since_id=since_id, limit=limit)
        except Exception as e:  # noqa: BLE001
            print(f"[newcards] new_leads retry {a}: {type(e).__name__}")
            await asyncio.sleep(1.2 * (a + 1))
    return {}


async def _collect_new_leads_30d(include_popup=False):
    """لیدهایی که «وضعیتِ فعلی‌شان new» است و در ۳۰ روزِ اخیر ساخته شده‌اند (قدیمی‌تر→جدیدتر).

    /new-leads صعودی بر اساسِ id است (سقفِ ۲۰۰) و فیلترِ status/تاریخِ سمتِ سرور ندارد؛ از بالا
    (جدیدترین) بلاک‌به‌بلاک تا عبور از مرزِ ۳۰ روز خزیده و اینجا فیلتر می‌شود.
    پیش‌فرض: پاپ‌آپِ ناشناسِ سایت حذف می‌شود (فقط لیدهای باتعامل)؛ include_popup=True همه را می‌آورد.
    """
    cutoff = (jdatetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    first = await _nl_retry(2_000_000_000, 1)
    mx = int((first or {}).get("max_id") or 0)
    if not mx:
        return []
    collected, hi, scanned = {}, mx, 0
    while hi > 0 and scanned < 6000:
        lo = max(0, hi - 200)
        r = await _nl_retry(lo, 200)
        ls = r.get("leads", []) if isinstance(r, dict) else []
        if not ls:
            break
        scanned += len(ls)
        for l in ls:
            collected[l.get("id")] = l
        oldest = min(ls, key=lambda x: x.get("id") or 0)
        if (oldest.get("created_local") or "") < cutoff:
            break
        hi = lo
    new30 = [l for l in collected.values()
             if l.get("status") == "new" and (l.get("created_local") or "") >= cutoff
             and (include_popup or (l.get("source") or "").lower() not in _POPUP_SOURCES)]
    new30.sort(key=lambda l: l.get("id") or 0)
    return new30


async def _filter_untouched(leads, header=None):
    """فقط لیدهایی که هیچ اکشنی رویشان ثبت نشده: بدونِ یادداشت/تاریخچهٔ وضعیت/تماس.

    نیاز به /profile به‌ازای هر لید دارد؛ چون مجموعهٔ باتعامل کوچک است به‌صرفه است.
    خطای API → لید نگه‌داشته می‌شود (در تردید حذف نکن).
    """
    out, total = [], len(leads)
    for i, l in enumerate(leads, 1):
        ph = crm.normalize_phone(l.get("phone"))
        try:
            p = await crm.get_profile(ph)
            lead = p.get("lead") or {}
            touched = (bool(p.get("notes")) or bool(p.get("status_log"))
                       or bool((lead.get("last_contact_at") or "").strip())
                       or bool((lead.get("notes_freetext") or "").strip()))
            if not touched:
                out.append(l)
        except Exception:  # noqa: BLE001 — در تردید نگه‌دار
            out.append(l)
        if header is not None and i % 15 == 0:
            try:
                await header.edit_text(f"🔎 در حال بررسیِ دست‌نخورده‌بودن… {worktasks._fa(i)}/{worktasks._fa(total)}")
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(0.2)
    return out


async def _safe_delete(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:  # noqa: BLE001 — ممکن است دستی پاک شده باشد
        pass


async def _send_newcard(bot, chat_id, lead, phone):
    from telegram.error import RetryAfter
    for _ in range(2):
        try:
            return await bot.send_message(chat_id, _newlead_text(lead), parse_mode=ParseMode.HTML,
                                          reply_markup=_newlead_kb(phone))
        except RetryAfter as e:  # سقفِ نرخ → همان‌قدر صبر و یک تلاشِ دیگر
            await asyncio.sleep(float(getattr(e, "retry_after", 5)) + 1)
        except Exception as e:  # noqa: BLE001
            print(f"[newcards] send {phone}: {e!r}")
            return None
    return None


async def _sync_newcards(bot, chat_id, leads, header):
    """گروه را با «لیدهای فعلاً جدید» هماهنگ می‌کند؛ هر مشتری فقط یک کارت (بدونِ تکرار):
    شماره‌ای که دیگر جدید نیست→کارتش پاک؛ شماره‌ای که کارتِ زنده دارد→دست‌نخورده؛ شماره‌ی تازه→کارتِ نو.
    خروجی: (تازه، ازقبل‌موجود، حذف‌شده).
    """
    current = {}
    for l in leads:
        p = crm.normalize_phone(l.get("phone"))
        if p:
            current.setdefault(p, l)
    prev = {p: mid for (p, mid) in db.newcard_phones(chat_id)}

    removed = 0
    for p, mid in prev.items():
        if p not in current:  # دیگر جدید نیست → پاک
            await _safe_delete(bot, chat_id, mid)
            db.newcard_delete(p, chat_id)
            removed += 1

    to_post = [(p, l) for p, l in current.items() if p not in prev]
    kept = len(current) - len(to_post)
    posted, total = 0, len(to_post)
    for i, (p, l) in enumerate(to_post, 1):
        m = await _send_newcard(bot, chat_id, l, p)
        if m:
            db.newcard_set(p, chat_id, m.message_id)
            posted += 1
        await asyncio.sleep(_NEWCARD_DELAY)
        if i % 20 == 0:
            try:
                await header.edit_text(f"📤 در حال ارسالِ کارت‌ها… {worktasks._fa(i)}/{worktasks._fa(total)}")
            except Exception:  # noqa: BLE001
                pass
    return posted, kept, removed


async def cmd_newcards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """‎/newleads → کارتِ لیدهایی که «آخرین وضعیتشان جدید» است، در ۳۰ روزِ اخیر ساخته شده‌اند،
    از منبعِ باتعامل‌اند (نه پاپ‌آپِ ناشناس) و «هیچ اکشنی رویشان نشده» را در همین گروه به‌صورتِ
    بوردِ بدونِ‌تکرار نگه می‌دارد. «/newleads all» پاپ‌آپ‌ها را هم (بدونِ فیلترِ اکشن) می‌آورد."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not (_authorized(update) or chat.id == _followup_group()):
        return
    if not crm.enabled():
        await msg.reply_text("اتصال CRM فعال نیست.")
        return
    if chat.id in _newcards_running:
        await msg.reply_text("⏳ ارسالِ لیدهای جدید همین حالا در جریان است؛ تا پایان صبر کن.")
        return
    arg = ((context.args or [""])[0] or "").strip().lower()
    include_all = arg in ("all", "همه", "popup", "پاپ‌اپ", "پاپ‌آپ")
    mode = "همه (با پاپ‌آپ)" if include_all else "باتعامل و دست‌نخورده"
    _newcards_running.add(chat.id)
    header = await msg.reply_text(f"📋 جمع‌آوریِ لیدهای «جدید»ِ ۳۰ روزِ اخیر — {mode}…")
    try:
        leads = await _collect_new_leads_30d(include_popup=include_all)
        if not include_all:  # فقط دست‌نخورده‌ها (بدونِ هیچ اکشن)؛ چون مجموعهٔ باتعامل کوچک است به‌صرفه است
            leads = await _filter_untouched(leads, header)
        if len(leads) > _NEWCARD_CAP:
            leads = leads[-_NEWCARD_CAP:]
        est = max(1, round(len(leads) * _NEWCARD_DELAY / 60))
        await header.edit_text(
            f"📤 {worktasks._fa(len(leads))} لیدِ «جدید»ِ {mode} — ارسالِ کارت‌ها (~{worktasks._fa(est)} دقیقه)…")
        posted, kept, removed = await _sync_newcards(context.bot, chat.id, leads, header)
        await header.edit_text(
            f"✅ <b>بوردِ لیدهای «جدید»ِ ۳۰ روزِ اخیر ({mode}) به‌روز شد.</b>\n"
            f"🆕 کارتِ تازه: {worktasks._fa(posted)}\n"
            f"✅ از قبل موجود (تکراری نشد): {worktasks._fa(kept)}\n"
            f"🗑️ حذفِ کارتِ لیدهایی که دیگر جدید نیستند: {worktasks._fa(removed)}\n"
            f"📊 مجموعِ لیدهای واجدِ شرایط: {worktasks._fa(len(leads))}\n"
            f"<i>برای دیدنِ پاپ‌آپ‌ها هم: /newleads all</i>",
            parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        print(f"[newcards] {e!r}")
        try:
            await header.edit_text(f"ارسالِ لیدهای جدید ناموفق: {type(e).__name__}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        _newcards_running.discard(chat.id)


async def _handle_lead(q):
    try:
        _, action, oid = q.data.split(":")
    except ValueError:
        await _safe_answer(q,)
        return
    user = q.from_user
    uname = (user.full_name if user else "") or (("@" + user.username) if (user and user.username) else str(user.id if user else 0))
    db.record_lead_outcome(int(oid), action, user.id if user else 0, uname)
    label = _LEAD_ACTIONS.get(action, action)
    stamp = reports.jalali_str(clock.tehran_now())
    text = q.message.text or ""
    base = text.split("\n📌 ")[0]
    m = re.search(r"📱\s*(\S+)", text)
    phone = m.group(1) if m else None
    try:
        await q.edit_message_text(f"{base}\n📌 {label} — {uname} • {stamp}", reply_markup=_lead_kb(oid, phone))
    except Exception:
        pass
    await _safe_answer(q,"ثبت شد ✅")


async def push_leads(app, days, statuses):
    """لیدهای جدیدِ ناموفق/لغو را با دکمه‌های اقدام به گروه پیگیری می‌فرستد.

    خروجی (sent, total) یا None اگر گروه پیگیری تنظیم نشده باشد.
    """
    group = _followup_group()
    if not group:
        return None
    leads = await reports.fetch_leads(days, statuses)
    sent = 0
    for o in leads:
        if db.lead_sent(o.get("id")):
            continue
        try:
            await app.bot.send_message(group, text=reports.lead_text(o), reply_markup=_lead_kb(o.get("id"), (o.get("billing") or {}).get("phone")))
            db.mark_lead(o.get("id"))
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.4)
        if sent >= 60:
            break
    return sent, len(leads)


async def _outcomes_report():
    rows = db.outcomes_since(time.time() - 30 * 86400)
    counts = {"contacted": 0, "noanswer": 0, "bought": 0}
    by_user = {}
    for _oid, action, _uid, uname, _ts in rows:
        counts[action] = counts.get(action, 0) + 1
        u = by_user.setdefault(uname or "—", {"total": 0, "bought": 0})
        u["total"] += 1
        if action == "bought":
            u["bought"] += 1
    lines = [
        "📊 نتایج پیگیری (۳۰ روز اخیر)",
        "",
        f"📞 تماس شد: {counts['contacted']}",
        f"🚫 پاسخ نداد: {counts['noanswer']}",
        f"✅ خرید کرد: {counts['bought']}",
    ]
    if by_user:
        lines += ["", "به تفکیک کارمند:"]
        for u, d in sorted(by_user.items(), key=lambda x: -x[1]["total"]):
            lines.append(f"• {u}: {d['total']} اقدام (✅ {d['bought']} خرید)")
    else:
        lines += ["", "— هنوز اقدامی ثبت نشده —"]
    return "\n".join(lines)


def _shift_start_epoch() -> float:
    """epochِ «امروز ساعت ۱۰ صبحِ تهران» (شروعِ شیفت)؛ هرگز در آینده نیست."""
    now = clock.tehran_now()
    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    return time.time() - max(0.0, (now - start).total_seconds())


def _shift_summary_text() -> str:
    """جمع‌بندیِ فعالیتِ امروزِ هر اپراتور (اقدامات CRM + نتایجِ لیدها) از شروعِ شیفت."""
    cutoff = _shift_start_epoch()
    blank = {"bought": 0, "contacted": 0, "noanswer": 0, "status": 0, "note": 0, "assign": 0, "followup": 0}
    agg: dict[str, dict] = {}
    for _phone, action, _detail, _uid, name, _ts in db.crm_actions_since(cutoff):
        d = agg.setdefault(name or "—", dict(blank))
        if action in d:
            d[action] += 1
    for _oid, action, _uid, name, _ts in db.outcomes_since(cutoff):
        d = agg.setdefault(name or "—", dict(blank))
        if action in d:
            d[action] += 1

    head = "📋 <b>جمع‌بندیِ پایانِ شیفت</b> (۱۰ تا ۱۹)\n🗓️ " + reports.jalali_str(clock.tehran_now())
    if not agg:
        return head + "\n\nامروز فعالیتی ثبت نشد."
    lines = [head, ""]
    order = [("bought", "🟢 خرید"), ("contacted", "📞 تماس"), ("noanswer", "🔕 بی‌پاسخ"),
             ("status", "🔁 وضعیت"), ("note", "📝 یادداشت"), ("assign", "👤 اساین"),
             ("followup", "⏰ پیگیری")]
    def _conv(d):
        eng = d["bought"] + d["contacted"] + d["noanswer"]
        return (100 * d["bought"] / eng) if eng else -1  # -1 = بدونِ تماس (تبدیل نامحاسبه)

    best = max((( _conv(d), n) for n, d in agg.items()), default=(-1, None))
    grand = 0
    for name, d in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
        parts = [f"{lbl} {d[key]}" for key, lbl in order if d.get(key)]
        grand += sum(d.values())
        conv = _conv(d)
        ctxt = f"  ·  📈 تبدیل {conv:.0f}%" if conv >= 0 else ""
        crown = " 🏆" if best[1] == name and best[0] > 0 else ""
        lines.append(f"• <b>{html.escape(name)}</b>{crown}: " + (" · ".join(parts) if parts else "—") + ctxt)
    lines += ["", f"جمعِ کل: {grand} اقدام"]
    return "\n".join(lines)


_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(_FA)


_LEAD_SCAN = 400  # چند لیدِ اخیر برای یافتنِ رسیدگی‌نشده‌های انباشتی اسکن شود (هر صفحه سقفِ ۲۰۰)


async def _scan_recent_leads(scan: int = _LEAD_SCAN):
    """آخرین ~scan لید را (صفحه‌بندی‌شده، از max_id به عقب) برمی‌گرداند + max_id.

    اندپوینت سقفِ ۲۰۰ در هر درخواست دارد؛ پس از (max_id − scan) به بالا صفحه‌به‌صفحه می‌خوانیم
    تا رسیدگی‌نشده‌های چند روزِ اخیر (نه فقط امروز) هم دیده شوند.
    """
    mx = int((await crm.new_leads(since_id=2_000_000_000, limit=1)).get("max_id") or 0)
    since = max(0, mx - scan)
    out, guard = {}, 0
    while guard < 6:
        guard += 1
        leads = (await crm.new_leads(since_id=since, limit=200)).get("leads") or []
        if not leads:
            break
        for L in leads:
            out[L.get("id")] = L
        since = max(int(L.get("id") or 0) for L in leads)
        if since >= mx or len(leads) < 200:
            break
    return list(out.values()), mx


async def _summary_counts() -> dict:
    """لیدِ جدیدِ رسیدگی‌نشده و پیگیریِ انجام‌نشده — «انباشتی» (شاملِ روزهای قبل، نه فقط امروز).

    - لیدِ رسیدگی‌نشده = وضعیتش هنوز «جدید» (status=new) است؛ یعنی کارتِ بدونِ تغییر از روزهای قبل هم.
    - پیگیریِ انجام‌نشده = سررسیدشده‌ی تا ۱۴ روزِ قبل که هنوز اقدامی رویش نشده.
    در خطای CRM مقدار None می‌ماند تا خطِ خلاصه «—» نشان دهد.
    """
    import poller  # واردسازیِ تنبل: پرهیز از حلقه‌ی import (poller خودش telegram_io را import می‌کند)
    now = clock.tehran_now()
    acted = {row[0] for row in db.crm_actions_since(_shift_start_epoch())}  # شماره‌های اقدام‌شده‌ی امروز
    out = {"fu_total": None, "fu_done": None, "fu_rem": None, "fu_remaining": [],
           "nl_total": None, "nl_done": None, "nl_rem": None, "nl_remaining": []}
    try:  # پیگیری‌های سررسیدشده‌ی انباشتی (تا ۱۴ روزِ قبل)
        after = (now - poller._DUE_WINDOW).strftime("%Y-%m-%d %H:%M")
        before = now.strftime("%Y-%m-%d %H:%M")
        recent = poller._recent_due(await crm.due_leads(after=after, before=before, limit=200))
        rem = [d for d, _k in recent if crm.normalize_phone(d.get("phone")) not in acted]
        out.update(fu_total=len(recent), fu_rem=len(rem), fu_done=len(recent) - len(rem), fu_remaining=rem)
    except Exception as e:
        print(f"[summary] پیگیری خطا: {e!r}")
    try:  # لیدهای جدیدِ رسیدگی‌نشده‌ی انباشتی (آخرین ~۴۰۰ لید که هنوز «جدید»اند)
        leads, mx = await _scan_recent_leads()
        today_base = (int(db.get_meta("newlead_day_id") or 0)
                      if db.get_meta("newlead_day_date") == now.strftime("%Y-%m-%d") else mx)
        nl_today = sum(1 for L in leads if int(L.get("id") or 0) > today_base)
        rem = [L for L in leads if L.get("status") == "new" and L.get("phone")
               and crm.normalize_phone(L["phone"]) not in acted]
        rem.sort(key=lambda L: int(L.get("id") or 0))  # قدیمی‌ترین رسیدگی‌نشده‌ها اول
        out.update(nl_total=nl_today, nl_done=None, nl_rem=len(rem), nl_remaining=rem)
    except Exception as e:
        print(f"[summary] لیدِ جدید خطا: {e!r}")
    return out


def _summary_counts_lines(c: dict) -> str:
    """دو خطِ «انباشتی»: لیدِ جدیدِ رسیدگی‌نشده و پیگیریِ انجام‌نشده (شاملِ روزهای قبل)."""
    def n(v):
        return "—" if v is None else _fa(v)
    nl = f"🆕 لیدِ جدیدِ رسیدگی‌نشده (شاملِ روزهای قبل): <b>{n(c['nl_rem'])}</b>"
    if c.get("nl_total") is not None:
        nl += f"  ·  از امروز: {_fa(c['nl_total'])}"
    fu = f"⏰ پیگیریِ انجام‌نشده (شاملِ روزهای قبل): <b>{n(c['fu_rem'])}</b>"
    return nl + "\n" + fu


_REST_BATCH = 40  # چند لیدِ رسیدگی‌نشده در هر بار زدنِ دکمه به گروه برود (بقیه در دفعاتِ بعد)


def _summary_kb(c: dict):
    """دکمه‌ی «ارسالِ کارت‌های باقی‌مانده» — فقط اگر باقی‌مانده‌ای باشد."""
    n = len(c.get("fu_remaining") or []) + len(c.get("nl_remaining") or [])
    if n <= 0:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        f"📤 ارسالِ باقی‌مانده‌ها به گروه ({_fa(n)} مورد)", callback_data="restcards:send")]])


async def _send_remaining_cards(q, context) -> None:
    """کارت‌های لید/پیگیریِ باقی‌مانده‌ی انباشتی را به گروهِ پیگیری می‌فرستد (ادمین‌محور، دسته‌ای).

    لیدهای رسیدگی‌نشده دسته‌به‌دسته (هر بار تا _REST_BATCH) و بدونِ تکرارِ همان‌روز فرستاده می‌شوند؛
    پس با رسیدگی/زدنِ دوباره‌ی دکمه، دسته‌ی بعدیِ باقی‌مانده‌ها (از قدیمی‌ترین) می‌رود.
    """
    await _safe_answer(q, "در حال ارسالِ باقی‌مانده‌ها…")
    group = _followup_group()
    if not group:
        await _safe_answer(q, "گروهِ پیگیری تنظیم نشده.", show_alert=True)
        return
    today = clock.tehran_now().strftime("%Y-%m-%d")
    c = await _summary_counts()
    sent = 0
    for d in (c["fu_remaining"] or [])[:60]:  # پیگیری‌های سررسیدشده (dedup با next_follow_up)
        try:
            phone = d.get("phone") or ""
            await context.bot.send_message(group, _due_text(d), parse_mode=ParseMode.HTML,
                                           reply_markup=_due_kb(phone))
            db.mark_due_sent(f"{phone}|{d.get('next_follow_up_gmt') or ''}")
            sent += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"[restcards] پیگیری {d.get('phone')}: {e!r}")
    nl_sent = 0
    for L in (c["nl_remaining"] or []):  # لیدهای رسیدگی‌نشده — دسته‌ای و بدونِ تکرارِ همان‌روز
        if nl_sent >= _REST_BATCH:
            break
        phone = L.get("phone") or ""
        key = f"nl:{crm.normalize_phone(phone)}:{today}"
        if not phone or db.due_sent(key):
            continue
        try:
            await context.bot.send_message(group, _newlead_text(L), parse_mode=ParseMode.HTML,
                                           reply_markup=_newlead_kb(phone))
            db.mark_due_sent(key)
            nl_sent += 1
            sent += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"[restcards] لید {L.get('phone')}: {e!r}")
    remain = max(0, (c.get("nl_rem") or 0) - nl_sent)
    tail = f"📤 {_fa(sent)} کارتِ باقی‌مانده به گروه ارسال شد."
    if remain > 0:
        tail += (f"\nهنوز ~{_fa(remain)} لیدِ رسیدگی‌نشده مانده؛ پس از رسیدگی یا با زدنِ دوباره‌ی دکمه، "
                 "دسته‌ی بعدی (از قدیمی‌ترین) می‌رود.")
    try:
        await context.bot.send_message(q.message.chat_id, tail)
    except Exception:
        pass


def _due_text(d: dict) -> str:
    """متنِ یک یادآوریِ پیگیری."""
    name = d.get("name") or "—"
    phone = d.get("phone") or ""
    st = d.get("status_label") or d.get("status") or ""
    who = d.get("assigned_name") or "—"
    return (
        "⏰ <b>یادآوریِ پیگیری</b>\n"
        f"👤 {html.escape(name)} — <code>{html.escape(phone)}</code>\n"
        f"📌 وضعیت: {html.escape(st)}  ·  🧑‍💼 مسئول: {html.escape(who)}\n"
        f"🗓️ سررسید: {html.escape(d.get('next_follow_up') or '')}"
    )


def _due_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[crm_view.open_button(crm.normalize_phone(phone)),
                                  InlineKeyboardButton("✖️ بستن", callback_data="crm:close")]])


def _newlead_text(L: dict) -> str:
    """کارتِ فشرده‌ی لیدِ جدید برای گروه."""
    return (
        "🆕 <b>لیدِ جدید</b>\n"
        f"👤 {html.escape(L.get('name') or '—')} — <code>{html.escape(L.get('phone') or '')}</code>\n"
        f"🔖 منبع: {html.escape(crm.source_label(L.get('source')))}  ·  🧑‍💼 مسئول: {html.escape(L.get('assigned_name') or '—')}\n"
        f"🕒 {html.escape(L.get('created_local') or '')}"
    )


def _newlead_kb(phone: str) -> InlineKeyboardMarkup:
    """دکمه‌های یک‌لمسیِ لیدِ جدید (callbackها همان مسیرهای CRM)."""
    p = crm.normalize_phone(phone or "")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 تماس گرفتم", callback_data=f"crm:sst:{p}:called"),
         InlineKeyboardButton("✅ خرید کرد", callback_data=f"crm:sst:{p}:purchased")],
        [InlineKeyboardButton("👁️ دیده‌شده‌ها", callback_data=f"crm:viewed:{p}"),
         InlineKeyboardButton("🎯 پیشنهاد", callback_data=f"crm:recommend:{p}")],
        [InlineKeyboardButton("⏰ فردا پیگیری", callback_data=f"crm:setfu:{p}:1"),
         InlineKeyboardButton("👤 کارت کامل", callback_data=f"crm:open:{p}")],
        [InlineKeyboardButton("✖️ بستن", callback_data="crm:close")],
    ])


def _is_newlead(msg) -> bool:
    """آیا این پیام، کارتِ «لیدِ جدید» است؟ (برای بروزرسانیِ فشرده در جا)"""
    return bool(msg and (msg.text or "").lstrip().startswith("🆕"))


async def _newlead_card_after(phone):
    """کارتِ فشردهٔ لیدِ جدید با وضعیتِ به‌روز (بعد از اقدام روی همان کارت)."""
    try:
        prof = await crm.get_profile(phone)
    except Exception:
        prof = {}
    lead = prof.get("lead") or {}
    c = prof.get("contact") or {}
    name = (f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
            or f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            or c.get("name") or lead.get("name") or "—")
    emoji = crm_view._STATUS_EMOJI.get(lead.get("status"), "🔘")
    label = lead.get("status_label") or lead.get("status") or "جدید"
    L = [
        "🆕 <b>لیدِ جدید</b>",
        f"👤 {html.escape(str(name))} — <code>{html.escape(phone)}</code>",
        f"{emoji} وضعیت: <b>{html.escape(str(label))}</b>",
    ]
    if lead.get("assigned_name"):
        L.append(f"🧑‍💼 مسئول: {html.escape(str(lead.get('assigned_name')))}")
    if lead.get("next_follow_up"):
        L.append(f"⏰ پیگیریِ بعدی: {html.escape(str(lead.get('next_follow_up')))}")
    return "\n".join(L)


def _recommend_text(phone: str, items: list) -> str:
    if not items:
        return f"🎯 فعلاً پیشنهادی برای <code>{html.escape(phone)}</code> آماده نیست."
    L = [f"🎯 <b>پیشنهادِ محصول</b> — <code>{html.escape(phone)}</code>", ""]
    for it in items[:12]:
        name = html.escape(str(it.get("product") or it.get("name") or "—"))
        url = it.get("url")
        title = f'<a href="{html.escape(str(url))}">{name}</a>' if url else f"<b>{name}</b>"
        line = f"• {title}"
        if it.get("price"):
            line += f" — {_toman(it.get('price'))} ت"
        if it.get("reason"):
            line += f"  <i>({html.escape(str(it.get('reason')))})</i>"
        L.append(line)
    return "\n".join(L)


def _back_only_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("« بازگشت", callback_data=f"crm:refresh:{phone}")]])


def _toman(val) -> str:
    try:
        return f"{int(float(val or 0)) // config.MONEY_DIVISOR:,}"
    except Exception:
        return str(val or "0")


def _jdate(iso: str) -> str:
    d = (iso or "")[:10]
    try:
        return jdatetime.date.fromgregorian(date=datetime.date.fromisoformat(d)).strftime("%Y/%m/%d") if d else ""
    except Exception:
        return d


def _orders_text(phone: str, orders: list) -> str:
    if not orders:
        return f"📦 سفارشی برای <code>{html.escape(phone)}</code> در ووکامرس پیدا نشد."
    paid = sum(int(float(o.get("total") or 0)) for o in orders if o.get("status") in config.PAID_STATUSES)
    L = [
        "📦 <b>سفارش‌های مشتری</b>",
        f"📞 <code>{html.escape(phone)}</code>  ·  {len(orders)} سفارش  ·  جمعِ خرید: {_toman(paid)} ت",
    ]
    for o in orders[:10]:
        num = o.get("number") or o.get("id")
        st = _STATUS_FA.get(o.get("status"), o.get("status") or "")
        em = _STATUS_EMOJI.get(o.get("status"), "•")
        items = "، ".join((i.get("name") or "")[:35] for i in (o.get("line_items") or [])[:3])
        L.append("➖➖➖➖➖➖➖➖➖➖")
        L.append(f"{em} <b>#{num}</b> — {html.escape(st)}")
        L.append(f"🗓️ {_jdate(o.get('date_created'))}  ·  💰 {_toman(o.get('total'))} ت")
        if items:
            L.append(f"🛍️ {html.escape(items)}")
    if len(orders) > 10:
        L.append(f"\n… و {len(orders) - 10} سفارشِ دیگر")
    return "\n".join(L)


def _viewed_text(phone: str, viewed: list) -> str:
    if not viewed:
        return f"👁️ موردی برای <code>{html.escape(phone)}</code> ثبت نشده."
    L = [f"👁️ <b>محصولاتِ دیده‌شده</b> — <code>{html.escape(phone)}</code>", ""]
    for v in viewed[:20]:
        name = v.get("product") or v.get("name") or "—"
        when = v.get("viewed_local") or v.get("viewed_at") or ""
        cnt = v.get("count")
        line = f"• {html.escape(str(name))}" + (f" ×{cnt}" if cnt else "")
        if when:
            line += f" — <i>{html.escape(str(when))}</i>"
        L.append(line)
    return "\n".join(L)


def _worklist_text(groups: dict) -> str:
    """لیستِ «کارِ امروز» به تفکیکِ همکار (groups: نامِ مسئول → فهرستِ لیدها)."""
    L = ["🌅 <b>کارِ امروز — پیگیری‌ها</b>", ""]
    for who, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        L.append(f"👤 <b>{html.escape(who)}</b> ({len(items)})")
        for d in items[:15]:
            L.append(f"   • {html.escape(d.get('name') or '—')} — <code>{html.escape(d.get('phone') or '')}</code>")
        if len(items) > 15:
            L.append(f"   … و {len(items) - 15} موردِ دیگر")
        L.append("")
    L.append("برای اقدام: شماره را با /crm باز کن، یا منتظرِ یادآوریِ سرِ‌تایم بمان.")
    return "\n".join(L)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not _authorized(update):
        return
    if update.effective_chat and update.effective_chat.type != "private":
        await msg.reply_text("🔒 منوی مدیریت فقط در چتِ خصوصی با ربات کار می‌کند.")
        return
    context.user_data["awaiting_search"] = False
    await msg.reply_text(_MENU_TITLE, reply_markup=_main_menu(), parse_mode=ParseMode.HTML)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    print(f"[cb] دریافت: {data} از {q.from_user.id if q.from_user else '?'}")
    if data.startswith("wt:"):  # گزارشِ کار: بستنِ تسک
        await worktasks.on_callback_hook(q, context)
        return
    if data.startswith("lead:"):  # دکمه‌های پیگیری در گروه — برای همه‌ی اعضای تیم
        await _handle_lead(q)
        return
    if data.startswith("crm:"):  # کارت/خواندنِ CRM — تیم در گروه
        if not _crm_can_read(q):
            await _safe_answer(q,"دسترسی ندارید.", show_alert=True)
            return
        await _handle_crm(q, context)
        return
    if data.startswith("restcards:"):  # ارسالِ باقی‌مانده‌ها به گروه — فقط ادمین (از پیوی یا گروه)
        if not q.from_user or q.from_user.id not in config.ADMIN_USER_IDS:
            await _safe_answer(q, "فقط مدیران.", show_alert=True)
            return
        await _send_remaining_cards(q, context)
        return
    if not q.from_user or q.from_user.id not in config.ADMIN_USER_IDS:
        await _safe_answer(q,"اجازه‌ی دسترسی ندارید.", show_alert=True)
        return
    if q.message and q.message.chat and q.message.chat.type != "private":  # گزارش‌ها فقط در پیوی
        await _safe_answer(q,"🔒 گزارش‌ها فقط در چتِ خصوصی با ربات.", show_alert=True)
        return
    await _safe_answer(q,)
    if data != "search":
        context.user_data["awaiting_search"] = False
    try:
        if data == "search":
            context.user_data["awaiting_search"] = True
            await q.edit_message_text(
                "🔍 شماره تماس، نام مشتری، یا بخشی از نام محصول را بفرستید:",
                reply_markup=_back_kb(),
            )
        elif data == "menu:main":
            await q.edit_message_text(_MENU_TITLE, reply_markup=_main_menu(), parse_mode=ParseMode.HTML)
        elif data == "menu:months":
            await q.edit_message_text("📆 یک ماه را انتخاب کنید:", reply_markup=_months_menu(reports.current_jyear()))
        elif data.startswith("months:"):
            await q.edit_message_text("📆 یک ماه را انتخاب کنید:", reply_markup=_months_menu(int(data.split(":")[1])))
        elif data == "menu:analytics":
            await q.edit_message_text("📈 آمار و تحلیل — یک گزینه را انتخاب کنید:", reply_markup=_analytics_menu())
        elif data == "rep:compare":
            await q.edit_message_text(await reports.report_compare(), reply_markup=_back_kb())
        elif data == "rep:topproducts":
            await q.edit_message_text(
                await reports.report_top_products(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:stats":
            await q.edit_message_text(
                await reports.report_stats(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:province":
            await q.edit_message_text(
                await reports.report_by_province(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:overview":
            await q.edit_message_text(
                await reports.report_overview(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:trend":
            await q.edit_message_text(await reports.report_trend(6), reply_markup=_back_kb())
        elif data == "rep:customers":
            await q.edit_message_text(
                await reports.report_top_customers(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:gwperf":
            await q.edit_message_text(
                await reports.report_gateway_performance(reports.current_jyear(), reports.current_jmonth()),
                reply_markup=_back_kb())
        elif data == "rep:pending":
            await q.edit_message_text(await reports.report_pending(), reply_markup=_back_kb())
        elif data == "followup":
            res = await push_leads(context.application, 7, ("failed",))
            if res is None:
                await q.edit_message_text(
                    "⚠️ گروه پیگیری تنظیم نشده.\nربات را در گروهِ پیگیری عضو کن و همان‌جا دستور /setfollowup را بفرست.",
                    reply_markup=_back_kb())
            else:
                sent, total = res
                await q.edit_message_text(
                    f"📞 {sent} لیدِ جدید به گروه پیگیری ارسال شد.\n"
                    f"(از {total} موردِ ۷ روز اخیر؛ موارد قبلاً‌ارسال‌شده دوباره فرستاده نمی‌شوند.)",
                    reply_markup=_back_kb())
        elif data == "outcomes":
            await q.edit_message_text(await _outcomes_report(), reply_markup=_back_kb())
        elif data == "csv:month":
            jy, jm = reports.current_jyear(), reports.current_jmonth()
            data_bytes = (await reports.orders_csv(jy, jm)).encode("utf-8-sig")
            await context.bot.send_document(
                chat_id=q.message.chat_id,
                document=InputFile(io.BytesIO(data_bytes), filename=f"sales_{jy}_{jm:02d}.csv"),
                caption=f"📄 سفارش‌های موفق {reports.J_MONTHS[jm - 1]} {jy}",
            )
            await q.edit_message_text("📄 فایل اکسل ارسال شد.", reply_markup=_back_kb())
        elif data == "rep:today":
            await q.edit_message_text(await reports.report("today"), reply_markup=_back_kb())
        elif data == "rep:week":
            await q.edit_message_text(await reports.report("week"), reply_markup=_back_kb())
        elif data == "rep:month":
            await q.edit_message_text(await reports.report("month"), reply_markup=_back_kb())
        elif data == "rep:year":
            await q.edit_message_text(await reports.report_jyear(reports.current_jyear()), reply_markup=_back_kb())
        elif data.startswith("jm:"):
            _, jy, jm = data.split(":")
            await q.edit_message_text(await reports.report_jmonth(int(jy), int(jm)), reply_markup=_back_kb())
    except Exception as e:
        if "not modified" in str(e).lower():  # محتوای یکسان — بی‌خطر، همان گزارش از قبل نمایش داده شده
            print(f"[cb] بدون تغییر: {data}")
        else:
            print(f"[cb] خطا: {data} -> {e!r}")
            try:
                await q.edit_message_text("⚠️ خطا در تهیه‌ی گزارش؛ بعداً دوباره امتحان کن.", reply_markup=_back_kb())
            except Exception:
                pass
    else:
        print(f"[cb] انجام شد: {data}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """یادداشت/جزئیاتِ CRM (ریپلای روی کارت/پرامپت) یا عبارتِ جستجوی سفارش."""
    msg = update.message
    if not msg:  # پیامِ ادیت‌شده / پستِ کانال → نادیده
        return
    if await worktasks.on_group_message(update, context):  # گروهِ گزارشِ کار: ثبتِ تسک/گزارش
        return
    # ثبتِ CRM با ریپلای (با Privacyِ گروه هم کار می‌کند)
    if msg.reply_to_message and crm.enabled():
        rep_text = msg.reply_to_message.text or ""
        is_site = "از کدام سایت خرید کرد" in rep_text
        is_prod = "کدام محصول ناموجود بود" in rep_text
        is_fu = "تاریخِ پیگیری را بنویس" in rep_text
        is_name = "نام و نام‌خانوادگی را بنویس" in rep_text
        is_note = (not is_site and not is_prod and not is_fu and not is_name) and (
            "یادداشتت را در ریپلای" in rep_text or "ریپلای کن و متن" in rep_text
        )
        if is_site or is_prod or is_fu or is_name or is_note:
            uid = update.effective_user.id if update.effective_user else 0
            chat_id = update.effective_chat.id if update.effective_chat else 0
            if not (uid in config.ADMIN_USER_IDS or chat_id == _followup_group()):
                return
            m = re.search(r"(?<!\d)0\d{10}(?!\d)", rep_text)
            val = (msg.text or "").strip()
            actor = _actor_name(update.effective_user)
            if not (m and val):
                await msg.reply_text("⚠️ شماره یا متن خوانده نشد؛ دوباره روی همان پیام ریپلای کن و متن را بنویس.")
                return
            phone = m.group(0)
            try:
                if is_site:
                    await crm.set_status(phone, "purchased_other_site", actor, other_site=val)
                    await msg.reply_text("✅ سایتِ خرید ثبت شد.")
                elif is_prod:
                    await crm.set_status(phone, "product_unavailable", actor, unavailable_product=val)
                    await msg.reply_text("✅ محصولِ ناموجود ثبت شد.")
                elif is_fu:
                    dt = _parse_followup(val)
                    if not dt:
                        await msg.reply_text("⚠️ فرمتِ تاریخ نامعتبر بود. مثال: ۱۴۰۵/۰۵/۰۱ یا 2026-07-01 10:30")
                        return
                    await crm.set_status(phone, "follow_up", actor, follow_up_at=dt)
                    db.record_crm_action(phone, "followup", uid, actor, detail=dt)
                    await msg.reply_text(f"✅ پیگیری برای {_to_jalali(dt)} ثبت شد.")
                elif is_name:
                    parts = val.split()
                    first = parts[0] if parts else ""
                    last = " ".join(parts[1:])
                    fields = {"first_name": first, "last_name": last}
                    r = await crm.update_fields(phone, "contact", fields, actor)
                    if not (r or {}).get("ok"):  # contact نبود → روی خودِ لید بنویس
                        r = await crm.update_fields(phone, "lead", fields, actor)
                    if (r or {}).get("ok"):
                        await msg.reply_text(f"✅ نام ثبت شد: {(first + ' ' + last).strip()}")
                    else:
                        await msg.reply_text("⚠️ ثبت نشد؛ این شماره در CRM رکوردِ معتبر ندارد.")
                else:
                    await crm.add_note(phone, val, actor)
                    db.record_crm_action(phone, "note", uid, actor)
                    await msg.reply_text("✅ یادداشت در CRM ثبت شد.")
            except Exception as e:
                print(f"[crm] ثبتِ ریپلای {phone}: {e!r}")
                await msg.reply_text("⚠️ ثبت نشد (خطای CRM). دوباره امتحان کن.")
            return

    if not _authorized(update) or not context.user_data.get("awaiting_search"):
        return
    context.user_data["awaiting_search"] = False
    query = (msg.text or "").strip()
    if not query:
        return
    chat_id = update.effective_chat.id
    await msg.reply_text(f"🔍 در حال جستجوی «{query}» …")
    try:
        orders = await woo.search_orders(query, per_page=10)
    except Exception as e:
        print(f"[search] {e!r}")
        await msg.reply_text("⚠️ خطا در جستجو؛ بعداً دوباره امتحان کن.")
        return
    if not orders:
        await update.message.reply_text("سفارشی با این مشخصات پیدا نشد.", reply_markup=_back_kb())
        return

    import pipeline  # واردسازی تنبل برای جلوگیری از حلقه‌ی ایمپورت

    for o in orders:
        try:
            photos, caption, _ = await pipeline.build_order_card(o)
            await send_card(context.application, chat_id, photos, caption)
        except Exception as e:
            print(f"[search] نمایش سفارش {o.get('id')}: {e!r}")
            await msg.reply_text(f"⚠️ نمایشِ سفارش {o.get('id')} ناموفق بود.")
        await asyncio.sleep(1)

    note = f"✅ {len(orders)} سفارش یافت شد."
    if len(orders) >= 10:
        note += " (نتایج زیاد بود؛ برای دقیق‌تر شدن عبارت دقیق‌تری بفرستید.)"
    await update.message.reply_text(note, reply_markup=_back_kb())


async def cmd_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not _authorized(update):
        return
    if update.effective_chat and update.effective_chat.type != "private":
        await msg.reply_text("🔒 گزارش‌ها فقط در چتِ خصوصی با ربات.")
        return
    if len(context.args) != 2:
        await msg.reply_text("فرمت درست: /range ۱۴۰۳/۰۱/۰۱ ۱۴۰۳/۰۱/۳۱")
        return
    try:
        await msg.reply_text(await reports.report("range", context.args))
    except Exception as e:
        print(f"[range] {e!r}")
        await msg.reply_text("⚠️ خطا در تهیه‌ی گزارش؛ بعداً دوباره امتحان کن.")


async def cmd_setfollowup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not _authorized(update):
        return
    chat = update.effective_chat
    db.set_meta("followup_group", str(chat.id))
    await msg.reply_text(f"✅ این گروه به‌عنوان گروه پیگیری تنظیم شد (id={chat.id}).")


async def cmd_fixcaptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کپشنِ سفارش‌های قبلیِ گروه را با تفکیکِ تخفیفِ جدید به‌روزرسانی می‌کند (فقط مدیر، ملایم/ضدبلاک).

    استفاده: /fixcaptions [روز]  (پیش‌فرض ۳۰ روزِ اخیر، سقفِ ۱۵۰ سفارش در هر اجرا).
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or user.id not in config.ADMIN_USER_IDS:
        return
    import pipeline
    days = 30
    if context.args and str(context.args[0]).isdigit():
        days = max(1, min(int(context.args[0]), 365))
    ids = db.tracked_orders(time.time() - days * 86400)
    cap = 150
    note = ""
    if len(ids) > cap:
        ids = ids[:cap]
        note = f" (سقفِ {_fa(cap)}؛ برای قدیمی‌ترها دوباره با روزِ کمتر اجرا کن)"
    await msg.reply_text(
        f"🔧 به‌روزرسانیِ کپشنِ {_fa(len(ids))} سفارشِ {_fa(days)} روزِ اخیر…{note}\nملایم انجام می‌شود (ضدبلاک).")
    edited = 0
    for oid in ids:
        try:
            before = (db.get_edit_row(oid) or (None, None, None, None, None))[3]
            await pipeline.rebuild_and_edit(context.application, oid)
            after = (db.get_edit_row(oid) or (None, None, None, None, None))[3]
            if after != before:
                edited += 1
        except Exception as e:  # noqa: BLE001
            print(f"[fixcaptions] {oid}: {e!r}")
        await asyncio.sleep(1.5)  # فاصله‌ی ملایم (ضدبلاکِ سایت + ضدِفلادِ تلگرام)
    await msg.reply_text(f"✅ تمام: {_fa(edited)} کپشن به‌روزرسانی شد از {_fa(len(ids))} سفارشِ بررسی‌شده.")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_menu))
    app.add_handler(CommandHandler("setfollowup", cmd_setfollowup))
    app.add_handler(CommandHandler("range", cmd_range))
    app.add_handler(CommandHandler("crm", cmd_crm))
    app.add_handler(CommandHandler("newleads", cmd_newcards))
    app.add_handler(CommandHandler("setworkgroup", worktasks.cmd_setworkgroup))
    app.add_handler(CommandHandler("work", worktasks.cmd_work))
    app.add_handler(CommandHandler("tasks", worktasks.cmd_tasks))
    app.add_handler(CommandHandler("report", worktasks.cmd_report))
    app.add_handler(CommandHandler("perf", worktasks.cmd_perf))
    app.add_handler(CommandHandler("perfmonth", worktasks.cmd_perfmonth))
    app.add_handler(CommandHandler("hours", worktasks.cmd_hours))
    app.add_handler(CommandHandler("igreport", igstats.cmd_igreport))
    app.add_handler(CommandHandler("igplan", worktasks.cmd_igplan))
    app.add_handler(CommandHandler("rivals", worktasks.cmd_rivals))
    app.add_handler(CommandHandler("igweekly", worktasks.cmd_igweekly))
    app.add_handler(CommandHandler("setigadmin", worktasks.cmd_setigadmin))
    app.add_handler(CommandHandler("linkwp", worktasks.cmd_linkwp))
    app.add_handler(CommandHandler("directives", worktasks.cmd_directives))
    app.add_handler(CommandHandler("crawl", worktasks.cmd_crawl))
    app.add_handler(CommandHandler("role", worktasks.cmd_role))
    app.add_handler(CommandHandler("health", worktasks.cmd_health))
    app.add_handler(CommandHandler("setup", worktasks.cmd_setup))
    app.add_handler(CommandHandler("fixcaptions", cmd_fixcaptions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
