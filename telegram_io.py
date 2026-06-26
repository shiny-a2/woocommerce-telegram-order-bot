"""سمت تلگرام: ارسال سفارش، ویرایش کپشن و دستورهای گزارش."""
from __future__ import annotations

import html

from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import config
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
    # موقعیت دقیقِ پلاگین جای مقدار محاسبه‌شده می‌نشیند (وقتی پلاگین ثبتش کرد)
    location = summary.get("location") or stock_location
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
    # وقتی اصلاح مالی هست، «مبلغ پرداختی» داخل بخش اصلاحات می‌آید (تکرار نشود)
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


async def post_order(app: Application, order, photos, caption) -> int:
    """photos: لیستی از بایت‌های JPEG. کپشن کامل روی عکس اول می‌نشیند."""
    chat_id = config.TELEGRAM_GROUP_ID
    if photos:
        media_items = []
        for i, data in enumerate(photos):
            if i == 0:
                media_items.append(
                    InputMediaPhoto(media=data, caption=caption, parse_mode=ParseMode.HTML)
                )
            else:
                media_items.append(InputMediaPhoto(media=data))
        msgs = await app.bot.send_media_group(chat_id=chat_id, media=media_items)
        return msgs[0].message_id
    msg = await app.bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML)
    return msg.message_id


async def edit_caption(app: Application, message_id, chat_id, caption):
    """کپشن یک پیام قبلی را ویرایش می‌کند (بدون محدودیت زمانی برای پیام‌های خود ربات)."""
    await app.bot.edit_message_caption(
        chat_id=chat_id, message_id=message_id, caption=caption, parse_mode=ParseMode.HTML
    )


# ---------- دستورهای گزارش (فقط اعضای مجاز) ----------

def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in config.ADMIN_USER_IDS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "ربات فعال است.\n"
        "دستورها:\n"
        "/sales — فروش امروز\n"
        "/week — فروش این هفته\n"
        "/month — فروش این ماه\n"
        "/range ۱۴۰۳/۰۱/۰۱ ۱۴۰۳/۰۱/۳۱ — بازه‌ی دلخواه"
    )


async def _send_report(update, kind):
    if not _authorized(update):
        return
    try:
        await update.message.reply_text(await reports.report(kind))
    except Exception as e:
        await update.message.reply_text(f"خطا در گزارش: {e}")


async def cmd_sales(update, context):
    await _send_report(update, "today")


async def cmd_week(update, context):
    await _send_report(update, "week")


async def cmd_month(update, context):
    await _send_report(update, "month")


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


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("sales", cmd_sales))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("range", cmd_range))
