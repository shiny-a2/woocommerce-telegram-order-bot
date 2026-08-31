"""انتسابِ فروش به اپراتورِ CRM — «تماس→خرید» با پنجرهٔ ۷ روز (بدونِ کوپن).

قاعدهٔ مالک: یک سفارش «کارِ اپراتور» است اگر پیش از ثبتِ سفارش، در پنجرهٔ ۷ روزه، اپراتور روی همان
تلفن کار کرده باشد (لیدِ اساین‌شده با last_contact، یا status_change/note_added/update در audit_log).
تماس باید **قبل** از سفارش باشد (ضدِتقلب). بقیهٔ سفارش‌ها ارگانیک‌اند (مشتری خودش خریده).

مبدأ: دیتابیسِ CRM روی سرورِ سایت (SSH+SQL، فقط‌خواندنی). سفارش‌ها: WooCommerce API.
هیچ چیزی نمی‌نویسد؛ فقط محاسبه + گزارش. صحت‌سنجی: زمان‌ها سمتِ سرورند (اپراتور جعل نمی‌کند).
"""
import datetime
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
SSH_HOST = "root@source-server.example"
SSH_KEY = os.path.join(_HERE, ".ssh", "jeweltime_ed25519")
CRM_DB = "gallery_db"
PFX = "crm_"
WINDOW_DAYS = 7

# اپراتورهای فروشِ تلفنی: {wp_user_id: نامِ نمایشی}. کارشناسِ فروش (تلگرام 0 ↔ وردپرس 0).
OPERATORS = {0: "کارشناسِ فروش"}

TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def _today_bounds():
    """مرزِ «امروزِ تهران» به‌صورتِ UTCِ نایو + تاریخِ گرگوریِ امروزِ تهران.

    audit_log و last_contact_at روی سرور UTC ذخیره می‌شوند، ولی سفارش‌های ووکامرس تهران‌اند؛
    این تابع بازهٔ دقیقِ روزِ تهران را (در UTC) می‌دهد تا هر دو منبع درست به «امروز» فیلتر شوند.
    """
    import clock
    day = clock.tehran_now().date()
    start_local = datetime.datetime(day.year, day.month, day.day, tzinfo=TEHRAN)
    end_local = start_local + datetime.timedelta(days=1)
    to_utc = lambda d: d.astimezone(datetime.timezone.utc).replace(tzinfo=None)  # noqa: E731
    return to_utc(start_local), to_utc(end_local), day


def norm_phone(p) -> str:
    """آخرین ۱۰ رقم (موبایلِ ایران) برای مچِ مقاوم. رشتهٔ خالی اگر نامعتبر."""
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    if not d:
        return ""
    return d[-10:] if len(d) >= 10 else d


def _sql(query: str) -> list:
    """اجرای SELECT روی دیتابیسِ CRM با SSH (stdin)، خروجی = list[list[str]] (تب‌جدا)."""
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=20", SSH_HOST,
           f"mysql {CRM_DB} -N --default-character-set=utf8mb4"]
    r = subprocess.run(cmd, input=query.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ssh/mysql rc={r.returncode}: {r.stderr.decode('utf-8','replace')[:200]}")
    out = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        out.append(line.split("\t"))
    return out


def _parse_dt(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def fetch_operator_events(op_id: int, since_days=45) -> dict:
    """رویدادهای «کارِ» اپراتور روی تلفن‌ها: {norm_phone: [datetime,...]}. مبدأ: leads + audit_log."""
    since = (datetime.date.today() - datetime.timedelta(days=since_days)).isoformat()
    q = f"""SET NAMES utf8mb4;
-- ۱) لیدهای اساینِ اپراتور که تماس گرفته (last_contact_at)
SELECT phone, last_contact_at FROM {PFX}leads
  WHERE assigned_to={op_id} AND last_contact_at IS NOT NULL AND last_contact_at>='{since} 00:00:00';
-- ۲) اکشن‌های کارِ اپراتور روی لید (status/note/assign/update) → تلفنِ لید
SELECT l.phone, a.created_at
  FROM {PFX}audit_log a JOIN {PFX}leads l ON l.id=a.entity_id
  WHERE a.actor_id={op_id} AND a.entity_type='lead'
    AND a.action IN ('status_change','note_added','assigned','updated') AND a.created_at>='{since} 00:00:00';
-- ۳) اکشن‌های اپراتور روی مخاطب (تلفن از JSONِ changes)
SELECT JSON_UNQUOTE(JSON_EXTRACT(a.changes,'$.phone_primary')), a.created_at
  FROM {PFX}audit_log a
  WHERE a.actor_id={op_id} AND a.entity_type='contact'
    AND a.action IN ('updated','tg_update') AND a.created_at>='{since} 00:00:00'
    AND JSON_EXTRACT(a.changes,'$.phone_primary') IS NOT NULL;
"""
    ev = {}
    for row in _sql(q):
        if len(row) < 2:
            continue
        ph = norm_phone(row[0])
        dt = _parse_dt(row[1])
        if ph and dt:
            ev.setdefault(ph, []).append(dt)
    for ph in ev:
        ev[ph].sort()
    return ev


def fetch_assigned_phones(op_id: int) -> set:
    """تلفنِ لیدهای اساین‌شده به اپراتور (برای شمارشِ «اساین‌شده»)."""
    rows = _sql(f"SELECT phone FROM {PFX}leads WHERE assigned_to={op_id};")
    return {norm_phone(r[0]) for r in rows if r and norm_phone(r[0])}


async def fetch_orders(woo_mod, days=45) -> list:
    """سفارش‌های پرداخت‌شدهٔ اخیر: [{number, phone, ts, total, name, status}].

    فیلترِ status در پایتون (نه در API): این فروشگاه پرداختی‌ها را بیشتر زیرِ «deliver» می‌گذارد،
    نه «completed»؛ و API فهرستِ چند-status را با کاما درست ترکیب نمی‌کند. پس همه را می‌گیریم و
    با POST_STATUSES (processing/completed/deliver/delivered) فیلتر می‌کنیم.
    """
    import config
    paid = set(config.POST_STATUSES or ["processing", "completed", "deliver", "delivered"])
    after = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    out, page = [], 1
    fields = "id,number,status,total,date_created,date_created_gmt,billing"
    while True:
        batch = await woo_mod.get("orders", {"per_page": 100, "page": page, "after": after,
                                             "_fields": fields})
        if not batch:
            break
        for o in batch:
            if (o.get("status") or "") not in paid:
                continue
            b = o.get("billing") or {}
            _ts = _parse_dt(o.get("date_created"))            # تهران (نمایش/برچسبِ روز)
            _ts_utc = _parse_dt(o.get("date_created_gmt")) or _ts  # UTC (ریاضیِ انتساب/مرزِ روز)
            out.append({"number": o.get("number") or o.get("id"),
                        "phone": norm_phone(b.get("phone")),
                        "ts": _ts, "ts_utc": _ts_utc,
                        "total": float(o.get("total") or 0),
                        "name": (f"{b.get('first_name','')} {b.get('last_name','')}").strip(),
                        "status": o.get("status")})
        if len(batch) < 100:
            break
        page += 1
    return out


def attribute(events: dict, orders: list, window_days=WINDOW_DAYS) -> dict:
    """هر سفارش را انتساب می‌دهد: attributed اگر تماسِ اپراتور در [order−window, order) باشد."""
    win = datetime.timedelta(days=window_days)
    attributed, organic, no_phone = [], [], []
    for o in orders:
        ots = o.get("ts_utc") or o.get("ts")  # همه‌چیز UTC: مچ با رویدادهای UTCِ CRM
        if not o["phone"] or not ots:
            no_phone.append(o)
            continue
        evs = events.get(o["phone"], [])
        hit = None
        for t in evs:
            if ots - win <= t < ots:  # تماسِ قبل از سفارش و در پنجره (هر دو UTC)
                hit = t  # آخرین تماسِ واجدِ شرط (چون مرتب است، همین ادامه می‌دهد)
        if hit is not None:
            gap_h = round((ots - hit).total_seconds() / 3600, 1)
            attributed.append({**o, "contact_ts": hit, "gap_hours": gap_h})
        else:
            organic.append(o)
    return {"attributed": attributed, "organic": organic, "no_phone": no_phone}


async def run(woo_mod, op_id: int, days=45) -> dict:
    op_name = OPERATORS.get(op_id, str(op_id))
    events = fetch_operator_events(op_id, since_days=days)
    assigned = fetch_assigned_phones(op_id)
    orders = await fetch_orders(woo_mod, days=days)
    plan = attribute(events, orders)
    attr = plan["attributed"]
    rev = sum(a["total"] for a in attr)
    contacted = len(events)
    # نرخِ تبدیل نسبت به تماس‌گرفته‌ها (نه اساین‌شده‌ها)
    conv = (len(attr) / contacted * 100) if contacted else 0
    return {"op_id": op_id, "op_name": op_name, "days": days,
            "assigned": len(assigned), "contacted": contacted,
            "orders_total": len(orders), "attributed": attr, "organic": plan["organic"],
            "no_phone": plan["no_phone"], "revenue_attributed": rev, "conversion_pct": conv}


def _fa(n):
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _money(x):
    try:
        return _fa(f"{int(float(x) / 10):,}")  # ریال→تومان
    except (TypeError, ValueError):
        return "۰"


def report_text(st: dict) -> str:
    lines = [
        f"📞 <b>کارنامهٔ فروشِ {st['op_name']}</b> — {_fa(st['days'])} روزِ اخیر",
        "",
        f"👥 لیدهای اساین‌شده: {_fa(st['assigned'])}",
        f"☎️ تلفن‌هایی که رویشان کار کرده: {_fa(st['contacted'])}",
        f"✅ فروشِ منتسب (کارِ او): <b>{_fa(len(st['attributed']))}</b> سفارش · "
        f"💰 {_money(st['revenue_attributed'])} تومان",
        f"📈 نرخِ تبدیل (فروش/تماس): {_fa(round(st['conversion_pct']))}٪",
        f"🛍️ کلِ سفارش‌های بازه: {_fa(st['orders_total'])} "
        f"(ارگانیک/مشتری خودش: {_fa(len(st['organic']))})",
    ]
    if st["attributed"]:
        lines.append("")
        lines.append("🎯 <b>فروش‌های منتسب:</b>")
        for a in sorted(st["attributed"], key=lambda x: -x["total"])[:15]:
            lines.append(f"• سفارش {_fa(a['number'])} — {_money(a['total'])}ت — "
                         f"{_fa(a['gap_hours'])} ساعت پس از تماس")
    return "\n".join(lines)


# ─────────────────────────  گزارشِ روزانهٔ «پایانِ شیفت»  ─────────────────────────
# گروهِ گزارشات: فعالیت + تعدادِ فروشِ منتسب (بدونِ مبلغ). مبالغ → فقط پی‌ویِ مدیر.

_ACTION_FA = {
    "note_added": "ثبتِ یادداشت/تماس",
    "status_change": "تغییرِ وضعیت",
    "assigned": "اساینِ لیدِ جدید",
    "updated": "ویرایشِ اطلاعات",
    "tg_update": "به‌روزرسانی از تلگرام",
    "created": "ایجادِ لید",
    "deleted": "حذفِ لید",
}


def fetch_daily_activity(op_id: int, start_utc, end_utc) -> dict:
    """فعالیتِ اپراتور در بازهٔ [start_utc, end_utc) (UTC): شمارشِ اکشن‌ها + تلفن‌های کارشده + اساینِ جدید."""
    s = start_utc.strftime("%Y-%m-%d %H:%M:%S")
    e = end_utc.strftime("%Y-%m-%d %H:%M:%S")
    q = f"""SET NAMES utf8mb4;
SELECT 'act' t, a.action k, COUNT(*) n FROM {PFX}audit_log a
  WHERE a.actor_id={op_id} AND a.created_at>='{s}' AND a.created_at<'{e}' GROUP BY a.action
UNION ALL
SELECT 'phones','', COUNT(DISTINCT l.phone) FROM {PFX}audit_log a JOIN {PFX}leads l ON l.id=a.entity_id
  WHERE a.actor_id={op_id} AND a.entity_type='lead' AND a.created_at>='{s}' AND a.created_at<'{e}'
UNION ALL
SELECT 'newassign','', COUNT(*) FROM {PFX}audit_log a
  WHERE a.actor_id={op_id} AND a.entity_type='lead' AND a.action='assigned'
    AND a.created_at>='{s}' AND a.created_at<'{e}'
UNION ALL
SELECT 'total','', COUNT(*) FROM {PFX}audit_log a
  WHERE a.actor_id={op_id} AND a.created_at>='{s}' AND a.created_at<'{e}';
"""
    acts, phones, newassign, total = {}, 0, 0, 0
    for row in _sql(q):
        if len(row) < 3:
            continue
        tag, key, num = row[0], row[1], row[2]
        try:
            num = int(num)
        except ValueError:
            continue
        if tag == "act":
            acts[key] = num
        elif tag == "phones":
            phones = num
        elif tag == "newassign":
            newassign = num
        elif tag == "total":
            total = num
    return {"actions": acts, "phones_worked": phones, "new_assigned": newassign, "total_actions": total}


async def run_daily(woo_mod, op_id: int, month_days=30) -> dict:
    """آمارِ «پایانِ شیفتِ امروز» + خلاصهٔ ماه‌تا‌کنون. زمان‌ها UTC؛ مرزِ روز = تهران."""
    op_name = OPERATORS.get(op_id, str(op_id))
    start_utc, end_utc, jday_g = _today_bounds()
    # رویدادها را برای انتساب تا (ماه + پنجرهٔ ۷روزه) عقب می‌کشیم تا سفارشِ امروز هم درست منتسب شود
    events = fetch_operator_events(op_id, since_days=month_days + WINDOW_DAYS)
    assigned_total = len(fetch_assigned_phones(op_id))
    orders = await fetch_orders(woo_mod, days=month_days + 1)
    plan = attribute(events, orders)

    def _today(o):
        t = o.get("ts_utc") or o.get("ts")
        return bool(t) and start_utc <= t < end_utc

    attr_month = plan["attributed"]
    attr_today = [a for a in attr_month if _today(a)]
    organic_today = [o for o in plan["organic"] if _today(o)]
    orders_today = [o for o in orders if _today(o)]
    activity = fetch_daily_activity(op_id, start_utc, end_utc)
    contacted_month = len(events)
    conv = (len(attr_month) / contacted_month * 100) if contacted_month else 0
    return {
        "op_id": op_id, "op_name": op_name, "jday_g": jday_g, "month_days": month_days,
        "assigned_total": assigned_total, "activity": activity,
        "orders_today_total": len(orders_today),
        "attr_today": attr_today, "organic_today": organic_today,
        "rev_today": sum(a["total"] for a in attr_today),
        "attr_month": attr_month, "rev_month": sum(a["total"] for a in attr_month),
        "contacted_month": contacted_month, "conversion_pct": conv,
    }


def _jlabel(gdate) -> str:
    import jdatetime
    return _fa(jdatetime.date.fromgregorian(date=gdate).strftime("%Y/%m/%d"))


def _unit() -> str:
    import config
    return getattr(config, "CURRENCY_LABEL", "تومان")


def report_group_daily(st: dict) -> str:
    """کارتِ گروهِ گزارشات: کاملِ فعالیت + تعدادِ فروشِ منتسب — بدونِ هیچ مبلغی."""
    acts = st["activity"]["actions"]
    L = [
        f"📅 <b>گزارشِ پایانِ شیفت — {st['op_name']}</b>",
        f"🗓 {_jlabel(st['jday_g'])}",
        "",
        "☎️ <b>کارِ امروز:</b>",
        f"• لیدهایی که رویشان کار کرد: {_fa(st['activity']['phones_worked'])}",
        f"• کلِ فعالیت‌های امروز: {_fa(st['activity']['total_actions'])}",
    ]
    for key in ("note_added", "status_change", "assigned", "updated", "tg_update", "created", "deleted"):
        n = acts.get(key, 0)
        if n:
            L.append(f"   — {_ACTION_FA.get(key, key)}: {_fa(n)}")
    for k, n in acts.items():  # اکشن‌های ناشناخته را هم صادقانه نشان بده
        if k not in _ACTION_FA and n:
            L.append(f"   — {k}: {_fa(n)}")
    L += [
        f"👥 کلِ لیدهای اساین‌شده به او: {_fa(st['assigned_total'])}",
        "",
        "✅ <b>فروشِ منتسب (کارِ او):</b>",
        f"• امروز: {_fa(len(st['attr_today']))} سفارش",
        f"• این ماه: {_fa(len(st['attr_month']))} سفارش",
        f"📈 نرخِ تبدیلِ ماه (فروش/لیدِ کارشده): {_fa(round(st['conversion_pct']))}٪",
        f"🛍️ سفارش‌های پرداختیِ امروزِ فروشگاه: {_fa(st['orders_today_total'])} "
        f"(ارگانیک/مشتریِ خودش: {_fa(len(st['organic_today']))})",
    ]
    return "\n".join(L)


def report_manager_money(st: dict) -> str:
    """کارتِ محرمانهٔ پی‌ویِ مدیر: فقط مبالغِ فروشِ منتسب (امروز + ماه) و ریزِ امروز."""
    import reports
    u = _unit()
    L = [
        f"🔒 <b>مبالغِ فروشِ {st['op_name']}</b> — محرمانه (فقط مدیر)",
        f"🗓 {_jlabel(st['jday_g'])}",
        "",
        f"💰 امروز: {_fa(len(st['attr_today']))} سفارش · "
        f"<b>{reports.fmt_money(st['rev_today'])}</b> {u}",
        f"💰 این ماه: {_fa(len(st['attr_month']))} سفارش · "
        f"<b>{reports.fmt_money(st['rev_month'])}</b> {u}",
    ]
    if st["attr_today"]:
        L.append("")
        L.append("🎯 <b>ریزِ فروشِ منتسبِ امروز:</b>")
        for a in sorted(st["attr_today"], key=lambda x: -x["total"])[:20]:
            L.append(f"• سفارش {_fa(a['number'])} — {reports.fmt_money(a['total'])} {u} — "
                     f"{_fa(a['gap_hours'])} ساعت پس از تماس")
    return "\n".join(L)
