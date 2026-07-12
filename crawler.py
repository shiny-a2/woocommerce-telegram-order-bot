"""خزشِ ملایمِ ضدبلاک: مشکلاتِ محصول (سایت) + پیگیریِ CRM + وضعیتِ اینستاگرام → مشکلاتِ عملی برای ساختِ تسک.

اصولِ ضدبلاک (تضمینی):
- فقط چند درخواستِ «شمارشی» (X-WP-Total)، نه اسکنِ کاملِ محصولات.
- به circuit-breakerِ woo.py احترام می‌گذارد: اگر سایت فشار/بلاک داشت، woo خطا می‌دهد و بخشِ سایت با یادداشت رد می‌شود
  (هرگز در حلقه دوباره نمی‌زند).
- اینستاگرام از گزارشِ کش‌شده‌ی سرویسِ ig-insights خوانده می‌شود؛ هیچ تماسِ مستقیمِ اینستاگرام از این‌جا نمی‌رود
  → صفر ریسکِ بلاکِ اینستاگرام.
- فقط دستی (با /crawl) اجرا می‌شود، نه خودکار.

خروجیِ collect(): (issues, notes) — هر issue یک {"key","text"} (key = دسته‌ی پایدارِ مشکل، برای جلوگیری
از تسکِ تکراری)، notes پیام‌های «در دسترس نیست».
"""
from __future__ import annotations

import asyncio
import datetime

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(_FA)


async def _site():
    import woo
    try:
        oos = await woo.total_count("products", {"status": "publish", "stock_status": "outofstock"})
        await asyncio.sleep(0.6)  # فاصله‌ی ملایم بینِ درخواست‌ها (ضدبلاک)
        drafts = await woo.total_count("products", {"status": "draft"})
        await asyncio.sleep(0.6)
        onhold = await woo.total_count("orders", {"status": "on-hold"})
    except Exception as e:  # noqa: BLE001 — circuit-open/بلاک/آشغال → رد (ضدبلاک)
        return [], f"محصولاتِ سایت موقتاً در دسترس نیست ({type(e).__name__})"
    issues = []
    if oos:
        issues.append({"key": "oos", "metric": oos, "dynamic": False,
                       "text": f"{_fa(oos)} محصولِ منتشرشده‌ی ناموجود در سایت — بررسی/شارژِ موجودی یا مخفی‌کردن"})
    if drafts:
        issues.append({"key": "drafts", "metric": drafts, "dynamic": False,
                       "text": f"{_fa(drafts)} محصولِ پیش‌نویسِ ناتمام — تکمیل و انتشار"})
    if onhold:
        issues.append({"key": "orders_onhold", "metric": onhold, "dynamic": True,
                       "text": f"{_fa(onhold)} سفارشِ «در انتظار» (on-hold) — بررسی/پیگیریِ پرداخت یا تکمیل"})
    return issues, ""


async def _crm():
    import crm
    import clock
    if not crm.enabled():
        return [], ""
    try:
        now = clock.tehran_now()
        after = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
        before = now.strftime("%Y-%m-%d %H:%M")
        due = await crm.due_leads(after=after, before=before, limit=100)
    except Exception as e:  # noqa: BLE001
        return [], f"CRM موقتاً در دسترس نیست ({type(e).__name__})"
    n = len(due or [])
    return ([{"key": "crm_due", "metric": n, "dynamic": True,
              "text": f"{_fa(n)} مشتریِ CRM با پیگیریِ سررسیدشده — تماس/پیگیری"}] if n else []), ""


async def _ig():
    import igstats
    if not igstats.enabled():
        return [], ""
    r = await igstats.summary()  # از ig-insightsِ کش‌شده — هیچ تماسِ مستقیمِ اینستاگرام
    if not r.get("ok"):
        return [], "آنالیزِ اینستاگرام فعلاً در دسترس نیست"
    issues = []
    if (r.get("posts_24h") or 0) == 0:
        issues.append({"key": "ig_nopost", "metric": 0, "dynamic": False,
                       "text": "امروز هیچ پستی در اینستاگرام گذاشته نشده — یک پست/استوریِ محصول بگذار"})
    g = r.get("growth_1d")
    if g is not None and g < 0:
        issues.append({"key": "ig_neg_growth", "metric": abs(int(g)), "dynamic": True,
                       "text": f"رشدِ فالوورِ اینستاگرامِ امروز منفی ({_fa(g)}) — یک اقدامِ جذب (ریلز/استوریِ تعاملی)"})
    if r.get("best_reach_post") or r.get("best_post"):
        issues.append({"key": "ig_promote", "metric": 0, "dynamic": False,
                       "text": "بهترین پستِ اخیرِ اینستاگرام را دوباره پروموت/استوری کن"})
    return issues, ""


async def collect():
    """(issues, notes) — مشکلاتِ عملی + یادداشت‌های در‌دسترس‌نبودن."""
    (si, sn), (ci, cn), (ii, inn) = await asyncio.gather(_site(), _crm(), _ig())
    issues = si + ci + ii
    notes = [x for x in (sn, cn, inn) if x]
    return issues, notes
