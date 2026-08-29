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
    fields = "id,number,status,total,date_created,billing"
    while True:
        batch = await woo_mod.get("orders", {"per_page": 100, "page": page, "after": after,
                                             "_fields": fields})
        if not batch:
            break
        for o in batch:
            if (o.get("status") or "") not in paid:
                continue
            b = o.get("billing") or {}
            out.append({"number": o.get("number") or o.get("id"),
                        "phone": norm_phone(b.get("phone")),
                        "ts": _parse_dt(o.get("date_created")),
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
        if not o["phone"] or not o["ts"]:
            no_phone.append(o)
            continue
        evs = events.get(o["phone"], [])
        hit = None
        for t in evs:
            if o["ts"] - win <= t < o["ts"]:  # تماسِ قبل از سفارش و در پنجره
                hit = t  # آخرین تماسِ واجدِ شرط (چون مرتب است، همین ادامه می‌دهد)
        if hit is not None:
            gap_h = round((o["ts"] - hit).total_seconds() / 3600, 1)
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
