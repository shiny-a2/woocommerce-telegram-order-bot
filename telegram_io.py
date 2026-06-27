"""سمت تلگرام: ارسال سفارش، ویرایش کپشن، منوی دکمه‌ای گزارش‌ها و جستجوی سفارش."""
from __future__ import annotations

import asyncio
import html
import io
import re
import time

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
import db
import reports
import woo

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
    if not summary.get("has_payment"):
        lines.append(f"💰 مبلغ پرداختی: {reports.fmt_money(f['total'])} {config.CURRENCY_LABEL}")
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
        InlineKeyboardButton("🚫 پاسخ نداد", callback_data=f"lead:noanswer:{oid}"),
        InlineKeyboardButton("✅ خرید کرد", callback_data=f"lead:bought:{oid}"),
    ]]
    link = _tg_link(phone)
    if link:
        rows.append([InlineKeyboardButton("💬 پیام در تلگرام", url=link)])
    return InlineKeyboardMarkup(rows)


def _followup_group():
    return int(db.get_meta("followup_group") or config.FOLLOWUP_GROUP_ID or 0)


async def _handle_lead(q):
    try:
        _, action, oid = q.data.split(":")
    except ValueError:
        await q.answer()
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
    await q.answer("ثبت شد ✅")


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


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    context.user_data["awaiting_search"] = False
    await update.message.reply_text(_MENU_TITLE, reply_markup=_main_menu(), parse_mode=ParseMode.HTML)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    if data.startswith("lead:"):  # دکمه‌های پیگیری در گروه — برای همه‌ی اعضای تیم
        await _handle_lead(q)
        return
    if not q.from_user or q.from_user.id not in config.ADMIN_USER_IDS:
        await q.answer("اجازه‌ی دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
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
        await q.edit_message_text(f"خطا در گزارش: {e}", reply_markup=_back_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت عبارت جستجو پس از زدن دکمه‌ی «جستجوی سفارش»."""
    if not _authorized(update) or not context.user_data.get("awaiting_search"):
        return
    context.user_data["awaiting_search"] = False
    query = (update.message.text or "").strip()
    if not query:
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"🔍 در حال جستجوی «{query}» …")
    try:
        orders = await woo.search_orders(query, per_page=10)
    except Exception as e:
        await update.message.reply_text(f"خطا در جستجو: {e}")
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
            await update.message.reply_text(f"خطا در نمایش سفارش {o.get('id')}: {e}")
        await asyncio.sleep(1)

    note = f"✅ {len(orders)} سفارش یافت شد."
    if len(orders) >= 10:
        note += " (نتایج زیاد بود؛ برای دقیق‌تر شدن عبارت دقیق‌تری بفرستید.)"
    await update.message.reply_text(note, reply_markup=_back_kb())


async def cmd_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) != 2:
        await update.message.reply_text("فرمت درست: /range ۱۴۰۳/۰۱/۰۱ ۱۴۰۳/۰۱/۳۱")
        return
    try:
        await update.message.reply_text(await reports.report("range", context.args))
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")


async def cmd_setfollowup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat = update.effective_chat
    db.set_meta("followup_group", str(chat.id))
    await update.message.reply_text(f"✅ این گروه به‌عنوان گروه پیگیری تنظیم شد (id={chat.id}).")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_menu))
    app.add_handler(CommandHandler("setfollowup", cmd_setfollowup))
    app.add_handler(CommandHandler("range", cmd_range))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
