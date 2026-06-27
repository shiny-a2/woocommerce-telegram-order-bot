"""گزارش فروش با بازه‌های تقویم شمسی (جلالی)."""
from __future__ import annotations

import datetime

import jdatetime

import clock
import config
import woo

# تبدیل ارقام فارسی/عربی به لاتین برای ورودی دستورها
_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(p): str(i) for i, p in enumerate("٠١٢٣٤٥٦٧٨٩")})

J_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def _now():
    """زمانِ واقعیِ تهران (مستقل از ساعتِ سرور)."""
    return clock.tehran_now()


def _jtoday():
    return jdatetime.date.fromgregorian(date=_now().date())


def current_jyear() -> int:
    return _jtoday().year


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
    g = _jtoday().togregorian()
    return datetime.datetime(g.year, g.month, g.day), _now(), "امروز"


def _range_week():
    jd = _jtoday()
    start = (jd - datetime.timedelta(days=jd.weekday())).togregorian()  # شنبه=۰
    return datetime.datetime(start.year, start.month, start.day), _now(), "این هفته"


def _range_month():
    jd = _jtoday()
    g = jdatetime.date(jd.year, jd.month, 1).togregorian()
    return datetime.datetime(g.year, g.month, g.day), _now(), f"{J_MONTHS[jd.month - 1]} {jd.year}"


def _parse_jdate(s: str):
    y, m, d = [int(x) for x in _norm_digits(s).split("/")]
    return jdatetime.date(y, m, d).togregorian()


def _jmonth_range(jy, jm):
    start = jdatetime.date(jy, jm, 1).togregorian()
    nxt = (jdatetime.date(jy, jm + 1, 1) if jm < 12 else jdatetime.date(jy + 1, 1, 1)).togregorian()
    s = datetime.datetime(start.year, start.month, start.day)
    e = datetime.datetime(nxt.year, nxt.month, nxt.day)
    return s, min(e, _now())


def _jyear_range(jy):
    start = jdatetime.date(jy, 1, 1).togregorian()
    nxt = jdatetime.date(jy + 1, 1, 1).togregorian()
    s = datetime.datetime(start.year, start.month, start.day)
    e = datetime.datetime(nxt.year, nxt.month, nxt.day)
    return s, min(e, _now())


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


def current_jmonth() -> int:
    return _jtoday().month


# ---------- آمار و تحلیل ----------

async def _orders_raw(start_dt, end_dt):
    return await woo.list_orders_in_range(start_dt.isoformat(), end_dt.isoformat())


def _is_paid(o):
    return (not config.PAID_STATUSES) or o.get("status") in config.PAID_STATUSES


def _sum_paid(orders):
    total, count = 0.0, 0
    for o in orders:
        if _is_paid(o):
            total += float(o.get("total") or 0)
            count += 1
    return total, count


def _prev_jmonth(jy, jm):
    return (jy, jm - 1) if jm > 1 else (jy - 1, 12)


def _jmonth_len(jy, jm):
    start = jdatetime.date(jy, jm, 1).togregorian()
    nxt = (jdatetime.date(jy, jm + 1, 1) if jm < 12 else jdatetime.date(jy + 1, 1, 1)).togregorian()
    return (nxt - start).days


async def report_compare():
    """مقایسه‌ی ماه جاری (تا امروز) با همان بازه از ماه قبل."""
    jt = _jtoday()
    cs = jdatetime.date(jt.year, jt.month, 1).togregorian()
    cur_total, cur_count = _sum_paid(await _orders_raw(
        datetime.datetime(cs.year, cs.month, cs.day), _now()))

    pjy, pjm = _prev_jmonth(jt.year, jt.month)
    pday = min(jt.day, _jmonth_len(pjy, pjm))
    ps = jdatetime.date(pjy, pjm, 1).togregorian()
    pe = jdatetime.date(pjy, pjm, pday).togregorian()
    prev_total, prev_count = _sum_paid(await _orders_raw(
        datetime.datetime(ps.year, ps.month, ps.day),
        datetime.datetime(pe.year, pe.month, pe.day, 23, 59, 59)))

    if prev_total:
        g = (cur_total - prev_total) / prev_total * 100
        growth = f"{'🟢 +' if g >= 0 else '🔴 '}{g:.0f}٪"
    else:
        growth = "—"
    return "\n".join([
        f"📊 مقایسه‌ی ماهانه (تا روز {jt.day})",
        "",
        f"▫️ {J_MONTHS[jt.month - 1]} (جاری): {fmt_money(cur_total)} {config.CURRENCY_LABEL} — {cur_count} سفارش",
        f"▫️ {J_MONTHS[pjm - 1]} (قبل): {fmt_money(prev_total)} {config.CURRENCY_LABEL} — {prev_count} سفارش",
        "",
        f"📈 رشد: {growth}",
    ])


async def report_top_products(jy, jm, limit=10):
    s, e = _jmonth_range(jy, jm)
    agg = {}
    for o in await _orders_raw(s, e):
        if not _is_paid(o):
            continue
        for li in o.get("line_items", []):
            nm = li.get("name") or "؟"
            a = agg.setdefault(nm, [0, 0.0])
            a[0] += li.get("quantity") or 0
            a[1] += float(li.get("total") or 0)
    top = sorted(agg.items(), key=lambda x: -x[1][1])[:limit]
    lines = [f"🏆 پرفروش‌ترین محصولات — {J_MONTHS[jm - 1]} {jy}", ""]
    for i, (nm, (q, rev)) in enumerate(top, 1):
        lines.append(f"{i}. {nm} — {int(q)} عدد، {fmt_money(rev)} {config.CURRENCY_LABEL}")
    if not top:
        lines.append("— موردی نبود —")
    return "\n".join(lines)


async def report_stats(jy, jm):
    s, e = _jmonth_range(jy, jm)
    orders = await _orders_raw(s, e)
    paid_total, paid_count = _sum_paid(orders)
    all_count = len(orders)
    bad = sum(1 for o in orders if o.get("status") in ("cancelled", "refunded", "failed"))
    aov = paid_total / paid_count if paid_count else 0
    rate = bad / all_count * 100 if all_count else 0
    return "\n".join([
        f"🧮 آمار کلی — {J_MONTHS[jm - 1]} {jy}",
        "",
        f"🧾 سفارش موفق: {paid_count}",
        f"💰 فروش کل: {fmt_money(paid_total)} {config.CURRENCY_LABEL}",
        f"📊 میانگین هر سفارش: {fmt_money(aov)} {config.CURRENCY_LABEL}",
        f"❌ لغو/مرجوع/ناموفق: {bad} ({rate:.0f}٪)",
    ])


async def report_by_province(jy, jm):
    s, e = _jmonth_range(jy, jm)
    agg = {}
    for o in await _orders_raw(s, e):
        if not _is_paid(o):
            continue
        b = o.get("billing", {}) or {}
        sh = o.get("shipping", {}) or {}
        code = (sh.get("state") if sh.get("address_1") else b.get("state")) or ""
        name = woo.state_name(code) or "نامشخص"
        agg[name] = agg.get(name, 0.0) + float(o.get("total") or 0)
    top = sorted(agg.items(), key=lambda x: -x[1])
    lines = [f"🗺️ فروش به تفکیک استان — {J_MONTHS[jm - 1]} {jy}", ""]
    for nm, amt in top[:15]:
        lines.append(f"• {nm}: {fmt_money(amt)} {config.CURRENCY_LABEL}")
    if not top:
        lines.append("— موردی نبود —")
    return "\n".join(lines)


async def report_pending():
    orders = await woo.get(
        "orders", {"status": "processing", "per_page": 50, "orderby": "date", "order": "asc"}
    )
    lines = [f"📦 در انتظار ارسال (در حال انجام): {len(orders)}", ""]
    for o in orders[:40]:
        f = caption_fields_brief(o)
        lines.append(f"#{f[0]} — {f[1]} — {fmt_money(f[2])} {config.CURRENCY_LABEL}")
    if not orders:
        lines.append("— موردی نیست —")
    return "\n".join(lines)


def caption_fields_brief(o):
    b = o.get("billing", {}) or {}
    name = f"{b.get('first_name', '')} {b.get('last_name', '')}".strip()
    return (o.get("number") or o.get("id"), name, o.get("total", ""))


async def orders_csv(jy, jm):
    """CSV سفارش‌های موفقِ یک ماه شمسی (متن با هدر فارسی)."""
    import csv
    import io

    s, e = _jmonth_range(jy, jm)
    orders = await _orders_raw(s, e)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["شماره", "تاریخ", "نام", "تماس", "استان", "محصول", "درگاه", "مبلغ(تومان)", "وضعیت"])
    for o in orders:
        if not _is_paid(o):
            continue
        b = o.get("billing", {}) or {}
        sh = o.get("shipping", {}) or {}
        code = (sh.get("state") if sh.get("address_1") else b.get("state")) or ""
        prods = "، ".join(li.get("name", "") for li in o.get("line_items", []))
        jd = jalali_str(o["date_created"]) if o.get("date_created") else ""
        name = f"{b.get('first_name', '')} {b.get('last_name', '')}".strip()
        w.writerow([
            o.get("number") or o.get("id"), jd, name, b.get("phone", ""),
            woo.state_name(code), prods, o.get("payment_method_title", ""),
            fmt_money(o.get("total")), o.get("status"),
        ])
    return buf.getvalue()


def tehran_now():
    return clock.tehran_now()


async def daily_summary_text():
    """خلاصه‌ی فروش «دیروزِ تهران» (برای ارسال خودکار راس نیمه‌شب تهران)."""
    y = (tehran_now() - datetime.timedelta(days=1)).date()  # دیروزِ تهران (میلادی)
    yj = jdatetime.date.fromgregorian(date=y)
    start = datetime.datetime(y.year, y.month, y.day)
    end = start + datetime.timedelta(days=1)
    label = f"{J_MONTHS[yj.month - 1]} {yj.day}، {yj.year}"
    body = _format_report(label, *(await _aggregate(start, end)))
    return "🌅 خلاصه‌ی فروش دیروز\n\n" + body
