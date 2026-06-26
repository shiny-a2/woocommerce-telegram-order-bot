"""گزارش فروش با بازه‌های تقویم شمسی (جلالی)."""
from __future__ import annotations

import datetime

import jdatetime

import config
import woo

# تبدیل ارقام فارسی/عربی به لاتین برای ورودی دستورها
_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(p): str(i) for i, p in enumerate("٠١٢٣٤٥٦٧٨٩")})

J_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def current_jyear() -> int:
    return jdatetime.date.today().year


def _norm_digits(s: str) -> str:
    return s.translate(_DIGIT_MAP)


def fmt_money(v):
    try:
        return f"{float(v) / config.MONEY_DIVISOR:,.0f}"
    except (TypeError, ValueError):
        return str(v)


def jalali_str(iso_or_dt):
    if isinstance(iso_or_dt, str):
        dt = datetime.datetime.fromisoformat(iso_or_dt.replace("Z", "+00:00"))
    else:
        dt = iso_or_dt
    return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d %H:%M")


def _range_today():
    g = jdatetime.date.today().togregorian()
    return datetime.datetime(g.year, g.month, g.day), datetime.datetime.now(), "امروز"


def _range_week():
    jd = jdatetime.date.today()
    start = (jd - datetime.timedelta(days=jd.weekday())).togregorian()  # شنبه=۰
    return datetime.datetime(start.year, start.month, start.day), datetime.datetime.now(), "این هفته"


def _range_month():
    jd = jdatetime.date.today()
    g = jdatetime.date(jd.year, jd.month, 1).togregorian()
    return datetime.datetime(g.year, g.month, g.day), datetime.datetime.now(), f"{J_MONTHS[jd.month - 1]} {jd.year}"


def _parse_jdate(s: str):
    y, m, d = [int(x) for x in _norm_digits(s).split("/")]
    return jdatetime.date(y, m, d).togregorian()


def _jmonth_range(jy, jm):
    start = jdatetime.date(jy, jm, 1).togregorian()
    nxt = (jdatetime.date(jy, jm + 1, 1) if jm < 12 else jdatetime.date(jy + 1, 1, 1)).togregorian()
    s = datetime.datetime(start.year, start.month, start.day)
    e = datetime.datetime(nxt.year, nxt.month, nxt.day)
    return s, min(e, datetime.datetime.now())


def _jyear_range(jy):
    start = jdatetime.date(jy, 1, 1).togregorian()
    nxt = jdatetime.date(jy + 1, 1, 1).togregorian()
    s = datetime.datetime(start.year, start.month, start.day)
    e = datetime.datetime(nxt.year, nxt.month, nxt.day)
    return s, min(e, datetime.datetime.now())


async def _aggregate(start_dt, end_dt):
    orders = await woo.list_orders_in_range(start_dt.isoformat(), end_dt.isoformat())
    by_gw = {}
    total = 0.0
    count = 0
    for o in orders:
        if config.PAID_STATUSES and o.get("status") not in config.PAID_STATUSES:
            continue
        gw = o.get("payment_method_title") or "نامشخص"
        amt = float(o.get("total") or 0)
        by_gw[gw] = by_gw.get(gw, 0.0) + amt
        total += amt
        count += 1
    return by_gw, total, count


def _format_report(label, by_gw, total, count):
    lines = [f"📊 گزارش فروش — {label}", f"🧾 تعداد سفارش: {count}", "", "به تفکیک درگاه:"]
    for gw, amt in sorted(by_gw.items(), key=lambda x: -x[1]):
        lines.append(f"• {gw}: {fmt_money(amt)} {config.CURRENCY_LABEL}")
    if not by_gw:
        lines.append("— سفارشی در این بازه نبود —")
    lines.append("")
    lines.append(f"💰 جمع کل: {fmt_money(total)} {config.CURRENCY_LABEL}")
    return "\n".join(lines)


async def report(kind, args=None):
    if kind == "today":
        s, e, label = _range_today()
    elif kind == "week":
        s, e, label = _range_week()
    elif kind == "month":
        s, e, label = _range_month()
    elif kind == "range":
        s = datetime.datetime.combine(_parse_jdate(args[0]), datetime.time.min)
        e = datetime.datetime.combine(_parse_jdate(args[1]), datetime.time.max)
        label = f"{args[0]} تا {args[1]}"
    else:
        raise ValueError("نوع گزارش نامعتبر است")
    return _format_report(label, *(await _aggregate(s, e)))


async def report_jmonth(jy, jm):
    s, e = _jmonth_range(jy, jm)
    return _format_report(f"{J_MONTHS[jm - 1]} {jy}", *(await _aggregate(s, e)))


async def report_jyear(jy):
    s, e = _jyear_range(jy)
    return _format_report(f"کل سال {jy}", *(await _aggregate(s, e)))
