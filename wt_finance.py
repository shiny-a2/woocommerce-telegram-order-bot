"""wt_finance.py — نمای فقط‌ادمینِ «حساب مالی»: خلاصهٔ مالیِ ماهانه از صفحهٔ پورسانتِ CRM.

مطابقِ ساختارِ واقعیِ commission-report.php (حسابِ «یک نفر»، لحظه‌ای‌محاسبه، واحدِ ریال؛ نمایش به تومان ÷۱۰).
- load_month(): حقوقِ ثابت (config) + مانده از قبل (خودکار = حقوقِ نهاییِ ماهِ قبل) را اعمال می‌کند.
- render(): خلاصهٔ ماه (بدونِ ریز). render_detail(): ریزِ دریافتی‌ها + پرداختی‌ها (دکمهٔ جدا).
تا وقتی اندپوینتِ CRM در دسترس نباشد پیامِ راهنما می‌دهد (بدونِ خطا).
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import reports

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_RIAL_TO_TOMAN = 10


def _fa(s) -> str:
    return str(s).translate(_FA)


def _money(rial) -> str:
    try:
        n = int(rial) // _RIAL_TO_TOMAN
    except (TypeError, ValueError):
        n = 0
    return f"{_fa(f'{n:,}').replace(',', '٬')} تومان"


def cur_month() -> str:
    return f"{reports.current_jyear():04d}-{reports.current_jmonth():02d}"


def month_label(month: str) -> str:
    try:
        jy, jm = month.split("-")
        return f"{reports.J_MONTHS[int(jm) - 1]} {_fa(int(jy))}"
    except Exception:  # noqa: BLE001
        return month


def _shift(month: str, delta: int) -> str:
    jy, jm = (int(x) for x in month.split("-"))
    jm += delta
    while jm < 1:
        jm += 12
        jy -= 1
    while jm > 12:
        jm -= 12
        jy += 1
    return f"{jy:04d}-{jm:02d}"


def _salary_bucket():
    return (int(getattr(config, "WT_FINANCE_FIXED_SALARY", 300_000_000)),
            int(getattr(config, "WT_FINANCE_BUCKET", 0)))


def _baseline():
    return getattr(config, "WT_FINANCE_CARRY_BASELINE", "1405-03")


_net_cache: dict = {}   # month -> (net_rial, ts) ؛ کشِ کوتاه برای زنجیرهٔ مانده
_NET_TTL = 180          # ثانیه (هم‌راستا با کشِ ۳ دقیقه‌ایِ سرور)


async def _monthly_net(month: str) -> int:
    """خالصِ همان ماه بدونِ مانده (= حقوق ثابت + پورسانت + مخارج − واریزشده). برای زنجیرهٔ مانده."""
    import time

    import crm
    hit = _net_cache.get(month)
    if hit and (time.time() - hit[1]) < _NET_TTL:
        return hit[0]
    salary, bucket = _salary_bucket()
    d = await crm.finance(month, fixed_salary=salary, carry=0, bucket=bucket)
    net = int((d.get("totals") or {}).get("final_pay", 0) or 0) if d.get("ok") else 0
    _net_cache[month] = (net, time.time())
    return net


async def load_month(month: str) -> dict:
    """finance ماه با حقوقِ ثابت + ماندهٔ زنجیره‌ای (از مبنای صفرِ خرداد ۱۴۰۵ به بعد).

    مانده = جمعِ خالصِ ماه‌های قبل از این ماه، از baseline تا ماهِ قبل. روندِ ماه‌به‌ماه در `_carry_chain` می‌آید.
    ماه‌های پیش از baseline: بدونِ زنجیره (carry=0).
    """
    salary, bucket = _salary_bucket()
    import crm
    baseline = _baseline()
    carry, chain = 0, []
    if month >= baseline:
        mm = baseline
        while mm < month:                       # جمعِ خالصِ baseline..month-1
            carry += await _monthly_net(mm)
            chain.append((mm, carry))            # موجودیِ پایانِ mm (= ماندهٔ ورودیِ ماهِ بعد)
            mm = _shift(mm, +1)
    d = await crm.finance(month, fixed_salary=salary, carry=carry, bucket=bucket)
    if isinstance(d, dict):
        d["_carry_chain"] = chain
        d["_before_baseline"] = month < baseline
    return d


def _err(reason) -> str:
    if reason == "no_endpoint":
        return ("💰 <b>حساب مالی</b>\n\n⏳ اندپوینتِ مالیِ CRM در دسترس نیست.\n"
                "اگر تازه ساخته شده، چند دقیقه بعد دوباره امتحان کن.")
    if reason == "crm_disabled":
        return "💰 <b>حساب مالی</b>\n\n⚠️ اتصالِ CRM تنظیم نشده (CRM_TG_URL/TOKEN)."
    if reason == "unreachable":
        return "💰 <b>حساب مالی</b>\n\n⚠️ سایت/CRM موقتاً در دسترس نیست. بعداً دوباره امتحان کن."
    return f"💰 <b>حساب مالی</b>\n\n⚠️ خطا در دریافتِ داده: {reason or 'نامشخص'}"


def _signed(rial) -> str:
    """عددِ تمیز؛ فقط برای منفی علامتِ منها می‌گذارد."""
    v = int(rial or 0)
    return f"−{_money(-v)}" if v < 0 else _money(v)


def render(data: dict, month: str) -> str:
    """خلاصهٔ ماه — اجزای حساب مرتب و زیرِ هم با تفکیک (ریز در دکمهٔ «دریافتی و پرداختی»)."""
    if not data or not data.get("ok"):
        return _err((data or {}).get("reason"))
    t = data.get("totals") or {}
    lbl = data.get("month_label") or month_label(data.get("month") or month)
    carry = int(t.get("carry", 0) or 0)
    L = [f"💰 <b>حساب مالی — {lbl}</b>", "<i>ارقام به تومان · کاملِ ماه</i>", "",
         "📊 <b>اجزای حساب:</b>"]
    L.append(f"📥 دریافتی‌ها:  {_money(t.get('receipts', 0))}")
    L.append(f"🧑‍💼 پورسانت:  {_money(t.get('commission', 0))}")
    cj, cn, cr = t.get("commission_jewel"), t.get("commission_non"), t.get("commission_remaining")
    if any(x is not None for x in (cj, cn, cr)):
        L.append(f"   ├ جواهرتایم:  {_money(cj or 0)}")
        L.append(f"   ├ غیرجواهرتایم:  {_money(cn or 0)}")
        L.append(f"   └ الباقی:  {_money(cr or 0)}")
    L.append(f"💵 حقوق ثابت:  {_money(t.get('fixed_salary', 0))}")
    L.append(f"🧾 مخارج (پرداختیِ تو):  {_money(t.get('expenses', 0))}")
    L.append(f"💳 واریزشده:  {_money(t.get('deposit', 0))}")
    chain = data.get("_carry_chain") or []
    if data.get("_before_baseline"):
        L.append("↩️ مانده از قبل:  — <i>(پیش از مبنای خرداد ۱۴۰۵)</i>")
    else:
        L.append(f"↩️ مانده از قبل:  {_signed(carry)} <i>(زنجیره‌ای)</i>")
        shown = chain[-6:]
        if len(chain) > 6:
            L.append("   ├ …")
        for i, (mm, bal) in enumerate(shown):
            tree = "└" if i == len(shown) - 1 else "├"
            L.append(f"   {tree} {month_label(mm).split(' ')[0]}:  {_signed(bal)}")
    L.append("━━━━━━━━━━━━━━")
    fp = int(t.get("final_pay", 0) or 0)
    if fp < 0:
        L.append(f"🔴 <b>مالی بدهکار:  {_money(-fp)}</b>")
    elif fp > 0:
        L.append(f"🟢 <b>مالی طلبکار:  {_money(fp)}</b>")
    else:
        L.append("⚪ <b>تسویه (صفر)</b>")
    L.append("<i>= حقوق ثابت + پورسانت + مخارج − واریزشده + مانده</i>")
    n_ord = len(data.get("orders") or [])
    L.append("")
    L.append(f"🧾 سفارش‌های سهیم: {_fa(n_ord)} · ریزِ دریافتی/پرداختی 👇")
    return "\n".join(L)


def render_detail(data: dict, month: str) -> str:
    """ریزِ دریافتی‌ها + پرداختی‌ها (مخارج) — نمای دکمهٔ جدا."""
    if not data or not data.get("ok"):
        return _err((data or {}).get("reason"))
    lbl = data.get("month_label") or month_label(data.get("month") or month)
    L = [f"📥 <b>دریافتی و پرداختیِ {lbl}</b>", "<i>ارقام به تومان</i>", ""]
    rr = data.get("receipts_rows") or []
    L.append(f"📥 <b>دریافتی‌ها ({_fa(len(rr))}):</b>")
    if rr:
        for r in rr[:40]:
            tag = " · خودکار" if str(r.get("source", "")).startswith("auto") else ""
            L.append(f"• {r.get('date', '')} — {r.get('desc') or '—'}: {_money(r.get('amount', 0))}{tag}")
    else:
        L.append("—")
    er = data.get("expenses_rows") or []
    L.append("")
    L.append(f"🧾 <b>پرداختی‌ها / مخارج ({_fa(len(er))}):</b>")
    if er:
        for e in er[:40]:
            tag = " · عودت" if e.get("source") == "auto_refund" else ""
            L.append(f"• {e.get('date', '')} — {e.get('desc') or '—'}: {_money(e.get('amount', 0))}{tag}")
    else:
        L.append("—")
    return "\n".join(L)


def summary_kb(data: dict, month: str) -> InlineKeyboardMarkup:
    rows = []
    prev_m = _shift(month, -1)
    nav = [InlineKeyboardButton(f"◀ {month_label(prev_m)}", callback_data=f"finance:{prev_m}")]
    if month < cur_month():
        nav.append(InlineKeyboardButton(f"{month_label(_shift(month, 1))} ▶", callback_data=f"finance:{_shift(month, 1)}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("📥 دریافتی و پرداختی", callback_data=f"yfin:rx:{month}")])
    if month != cur_month():
        rows.append([InlineKeyboardButton("↩️ ماهِ جاری", callback_data="finance:cur")])
    rows.append([InlineKeyboardButton("🔙 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def detail_kb(month: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 خلاصهٔ ماه", callback_data=f"finance:{month}")],
        [InlineKeyboardButton("🔙 منو", callback_data="menu:main")],
    ])
