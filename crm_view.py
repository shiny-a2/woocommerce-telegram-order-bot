"""نمایشِ کارتِ CRM در تلگرام — رندرِ خروجیِ /tg/profile به یک کارتِ تمیزِ فارسی.

فاز ۱: فقط خواندن (کارت + بروزرسانی). دکمه‌های نوشتن (فاز ۲) جداگانه اضافه می‌شوند.
"""
from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# نگاشتِ وضعیتِ لید به ایموجی
_STATUS_EMOJI = {
    "new": "🆕", "called": "📞", "no_answer": "🔕", "follow_up": "🟡",
    "purchased": "✅", "in_store": "🏬", "purchased_other_site": "🌐",
    "product_unavailable": "📦", "visit_only": "👀", "lost": "❌",
}

# enumِ وضعیت‌ها برای کیبوردِ تغییرِ وضعیت (فاز ۲)
STATUS_CHOICES = [
    ("called", "📞 تماس شد"), ("no_answer", "🔕 پاسخ نداد"), ("follow_up", "🟡 پیگیری"),
    ("purchased", "✅ خرید کرد"), ("in_store", "🏬 خرید حضوری"),
    ("purchased_other_site", "🌐 از سایت دیگر"), ("product_unavailable", "📦 ناموجود"),
    ("visit_only", "👀 فقط بازدید"), ("lost", "❌ منصرف"),
]

_LINE = "➖➖➖➖➖➖➖➖➖➖"


def _e(v) -> str:
    return html.escape(str(v)) if v not in (None, "") else ""


def is_followup(data: dict) -> bool:
    """آیا این لید در وضعیتِ پیگیری است؟ (برای مسیریابی به گروهِ مخصوص)"""
    lead = (data or {}).get("lead") or {}
    return lead.get("status") == "follow_up"


def render_profile(data: dict) -> str:
    """خروجیِ /tg/profile → متنِ کارت (HTML)."""
    if not data or not data.get("ok"):
        return "⚠️ خطا در دریافت اطلاعات CRM."
    if not data.get("found"):
        return f"🔍 برای شماره‌ی <code>{_e(data.get('phone'))}</code> رکوردی در CRM پیدا نشد."

    c = data.get("contact") or {}
    m = data.get("meta") or {}
    lead = data.get("lead") or {}
    notes = data.get("notes") or []
    slog = data.get("status_log") or []

    name = (
        f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        or f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
        or "بدون نام"
    )
    L: list[str] = []

    if data.get("blacklisted"):
        L.append("⛔ <b>این شماره در لیستِ سیاه است</b>")
        L.append("")

    L.append(f"👤 <b>{_e(name)}</b>")

    head = [f"📞 <code>{_e(data.get('phone'))}</code>"]
    if c.get("city"):
        head.append(f"📍 {_e(c.get('city'))}")
    L.append("  ·  ".join(head))

    sub = []
    if c.get("source"):
        sub.append(f"🔖 {_e(c.get('source'))}")
    if c.get("customer_label"):
        sub.append(f"🏷️ {_e(c.get('customer_label'))}")
    if sub:
        L.append("  ·  ".join(sub))

    extra = []
    if m.get("birth_date_j"):
        extra.append(f"🎂 {_e(m.get('birth_date_j'))}")
    if m.get("neighborhood"):
        extra.append(f"🏠 {_e(m.get('neighborhood'))}")
    if m.get("telegram_username"):
        extra.append(f"📨 @{_e(str(m.get('telegram_username')).lstrip('@'))}")
    if extra:
        L.append("  ·  ".join(extra))
    if c.get("email"):
        L.append(f"✉️ {_e(c.get('email'))}")

    L.append(_LINE)
    if lead:
        emoji = _STATUS_EMOJI.get(lead.get("status"), "🔘")
        L.append(f"🎯 <b>لید</b> · {emoji} {_e(lead.get('status_label') or lead.get('status') or 'نامشخص')}")
        if lead.get("other_site"):
            L.append(f"🌐 خرید از: {_e(lead.get('other_site'))}")
        if lead.get("unavailable_product"):
            L.append(f"📦 محصولِ ناموجود: {_e(lead.get('unavailable_product'))}")
        if lead.get("assigned_name"):
            L.append(f"👨‍💼 مسئول: {_e(lead.get('assigned_name'))}")
        if lead.get("next_follow_up"):
            L.append(f"⏰ پیگیریِ بعدی: {_e(lead.get('next_follow_up'))}")
        if lead.get("sla_due_at"):
            L.append(f"⏳ مهلتِ SLA: {_e(lead.get('sla_due_at'))}")
    else:
        L.append("🎯 <i>هنوز لیدی برای این مشتری ثبت نشده.</i>")

    if notes:
        L.append(_LINE)
        L.append("📝 <b>آخرین یادداشت‌ها</b>")
        for n in notes[:5]:
            L.append(f"• <i>{_e(n.get('created_local'))}</i> — {_e(n.get('author'))}: {_e(n.get('note'))}")

    if slog:
        L.append(_LINE)
        L.append("🔄 <b>تاریخچه‌ی وضعیت</b>")
        for s in slog[:5]:
            lbl = _e(s.get("status_label") or s.get("status"))
            L.append(f"• <i>{_e(s.get('created_local'))}</i> — {lbl} ← {_e(s.get('author'))}")

    L.append(_LINE)
    L.append("💬 <i>برای ثبتِ یادداشت، روی همین پیام ریپلای کن و متن را بنویس.</i>")
    return "\n".join(L)


# ---------- کیبوردها ----------
def read_kb(phone: str) -> InlineKeyboardMarkup:
    """کیبوردِ کارت در فاز ۱ (فقط خواندن)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"crm:refresh:{phone}"),
        InlineKeyboardButton("✖️ بستن", callback_data="crm:close"),
    ]])


def action_kb(phone: str) -> InlineKeyboardMarkup:
    """کیبوردِ کارت با اقدام‌ها (فاز ۲: وضعیت/اساین/یادداشت/پیگیری)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 تغییر وضعیت", callback_data=f"crm:mst:{phone}"),
         InlineKeyboardButton("👤 اساین به همکار", callback_data=f"crm:masg:{phone}")],
        [InlineKeyboardButton("📝 ثبت یادداشت", callback_data=f"crm:note:{phone}"),
         InlineKeyboardButton("⏰ تعیین پیگیری", callback_data=f"crm:fu:{phone}")],
        [InlineKeyboardButton("📦 سفارش‌ها", callback_data=f"crm:orders:{phone}"),
         InlineKeyboardButton("👁️ دیده‌شده", callback_data=f"crm:viewed:{phone}")],
        [InlineKeyboardButton("🎯 پیشنهادِ محصول", callback_data=f"crm:recommend:{phone}"),
         InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"crm:editname:{phone}")],
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"crm:refresh:{phone}"),
         InlineKeyboardButton("✖️ بستن", callback_data="crm:close")],
    ])


def followup_kb(phone: str) -> InlineKeyboardMarkup:
    """منوی تعیینِ تاریخِ پیگیری (گزینه‌های سریع + تاریخِ دلخواه)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("فردا", callback_data=f"crm:setfu:{phone}:1"),
         InlineKeyboardButton("۲ روز دیگر", callback_data=f"crm:setfu:{phone}:2")],
        [InlineKeyboardButton("۳ روز دیگر", callback_data=f"crm:setfu:{phone}:3"),
         InlineKeyboardButton("۷ روز دیگر", callback_data=f"crm:setfu:{phone}:7")],
        [InlineKeyboardButton("✏️ تاریخِ دلخواه", callback_data=f"crm:fucustom:{phone}")],
        [InlineKeyboardButton("« بازگشت", callback_data=f"crm:refresh:{phone}")],
    ])


def status_kb(phone: str) -> InlineKeyboardMarkup:
    """منوی انتخابِ وضعیتِ لید (۲ تایی در هر ردیف)."""
    rows, row = [], []
    for key, label in STATUS_CHOICES:
        row.append(InlineKeyboardButton(label, callback_data=f"crm:sst:{phone}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("« بازگشت", callback_data=f"crm:refresh:{phone}")])
    return InlineKeyboardMarkup(rows)


def assign_kb(phone: str, agents: list) -> InlineKeyboardMarkup:
    """منوی انتخابِ همکار برای اساین (فقط همکارانِ دارای شناسه‌ی معتبر)."""
    valid = [a for a in (agents or []) if str(a.get("user_id") or "").isdigit()][:20]
    rows = [[InlineKeyboardButton(f"👤 {a.get('display_name')}", callback_data=f"crm:sasg:{phone}:{a.get('user_id')}")]
            for a in valid]
    rows.append([InlineKeyboardButton("« بازگشت", callback_data=f"crm:refresh:{phone}")])
    return InlineKeyboardMarkup(rows)


def open_button(phone: str) -> InlineKeyboardButton:
    """دکمه‌ای برای بازکردنِ کارتِ CRM از روی پیامِ یک لید/سفارش."""
    return InlineKeyboardButton("👤 پروفایل CRM", callback_data=f"crm:open:{phone}")
