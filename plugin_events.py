"""خلاصه‌ی تمیزِ تغییرات پلاگین‌های A2 از یادداشت‌های سفارش، برای افزودن به کپشن.

خروجی dict:
- corrections: تعویض، اصلاح قیمت، و بخش مالیِ مرتب (مبلغ پرداختی → الباقی → جمع نهایی → عودت)
- operations:  «ثبت عملیات سفارش» (فاکتور دشت + جعبه/پاکت/گارانتی/باطری)
- location:    موقعیت موجودیِ دقیق از پلاگین (مثل «عطار») که جای مقدار محاسبه‌شده می‌نشیند
- has_payment: آیا «مبلغ پرداختی» داخل بخش اصلاحات آمده (تا در بالای کپشن تکرار نشود)
مبالغ ریال‌اند و به تومان تبدیل می‌شوند. «سایر/دیگر» به نام نمایشی نگاشت می‌شود.
"""
from __future__ import annotations

import re
from html import escape as _esc

import config

_DIGITS = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGITS.update({ord(p): str(i) for i, p in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _to_toman(num_str):
    if not num_str:
        return None
    s = re.sub(r"[^\d-]", "", num_str.translate(_DIGITS))
    if not s or s == "-":
        return None
    try:
        return f"{int(s) / config.MONEY_DIVISOR:,.0f}"
    except ValueError:
        return None


def _num_after(label, text):
    m = re.search(re.escape(label) + r"\s*([\d,٬۰-۹٠-٩-]+)", text)
    return m.group(1) if m else None


def _bracket_method(text):
    m = re.search(r"\[([^:\]]+)\s*:", text)
    label = m.group(1).strip() if m else "سایر"
    return config.PAYMENT_ALIASES.get(label, label)


def summarize(notes):
    swap = price = gateway = balance = final_total = refund = None
    ops = None
    location = None

    for n in sorted(notes or [], key=lambda x: x.get("id", 0)):  # قدیمی→جدید، جدید بازنویسی کند
        t = n.get("note", "") or ""

        if "تعویض انجام شد" in t:
            m = re.search(r"تعویض انجام شد:\s*«([^»]+)»\s*[←⬅]\s*(.+?)(?:\s*×|\s*$)", t)
            if m:
                swap = (m.group(1).strip(), m.group(2).strip())

        if "اصلاح قیمت آیتم" in t:
            inc = _to_toman(_num_after("مبلغ افزایش=", t))
            dec = _to_toman(_num_after("مبلغ کاهش=", t))
            if inc:
                price = f"افزایش {inc} تومان"
            elif dec:
                price = f"کاهش {dec} تومان"

        if "ثبت اصلاح" in t and "جمع نهایی" in t:
            final_total = _to_toman(_num_after("جمع نهایی=", t)) or final_total
            gw = re.search(r"پرداخت‌شده روی درگاه[^=]*=\s*([\d,٬۰-۹٠-٩-]+)", t)
            gateway = _to_toman(gw.group(1)) if gw else gateway
            bal = _to_toman(_num_after("دریافت خارج از درگاه=", t))
            balance = (bal, _bracket_method(t)) if (bal and bal != "0") else None
            rf = _to_toman(_num_after("عودت پیشنهادی=", t))
            refund = rf if (rf and rf != "0") else None

        if "عملیات سفارش" in t and "A2/DB" in t:
            d = {}
            for line in t.splitlines():
                line = line.strip()
                if not line or line.startswith("عملیات سفارش") or line.startswith("ذخیره توسط"):
                    continue
                m = re.match(r"(.+?)\s*[:：]\s*(.+)", line)
                if m:
                    d[m.group(1).strip()] = m.group(2).strip()
            if d:
                ops = d
                if d.get("موقعیت موجودی"):
                    location = d["موقعیت موجودی"]

    corrections = []
    if swap:
        corrections.append(f"🔄 تعویض : {_esc(swap[0])}")
        corrections.append(f"با : {_esc(swap[1])}")
    if price:
        corrections.append(f"💰 اصلاح قیمت آیتم: {price}")
    # بخش مالیِ مرتب: مبلغ پرداختی → الباقی → جمع نهایی → عودت
    if gateway:
        corrections.append(f"💳 مبلغ پرداختی: {gateway} تومان")
    if balance:
        corrections.append(f"💵 الباقی: {balance[0]} تومان (روش: {_esc(balance[1])})")
    if final_total:
        corrections.append(f"🧮 جمع نهایی: {final_total} تومان")
    if refund:
        corrections.append(f"↩️ عودت پیشنهادی: {refund} تومان")

    operations = []
    if ops:
        if ops.get("فاکتور دشت"):
            operations.append(f"فاکتور دشت: {_esc(ops['فاکتور دشت'])}")
        checklist = [f"{k}: {_esc(ops[k])}" for k in ("جعبه", "پاکت", "گارانتی", "باطری") if ops.get(k)]
        if checklist:
            operations.append("  ".join(checklist))

    return {
        "corrections": corrections,
        "operations": operations,
        "location": location,
        "has_payment": bool(gateway),
        "swap": swap,  # (نامِ ساعتِ جدید، نامِ ساعتِ قدیمی) یا None — برای گذاشتنِ عکسِ هر دو در کارت
    }
