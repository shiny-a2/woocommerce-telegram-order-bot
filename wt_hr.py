"""wt_hr.py — مدیریتِ سادهٔ پرسنل + حضور + حقوقِ ساده (بدونِ payroll engine پیچیده).

سه سرویسِ کوچک با نویسندهٔ یگانه (مثلِ taskservice):
  - Personnel : wt_personnel (افزودن/ویرایش/فعال‌سازی؛ بدونِ حذفِ سخت)
  - Attendance: wt_attendance (رویدادهای ورود/خروج؛ روی همان مسیرِ گزارشِ موجود؛ dedup؛ اصلاحِ نرم)
  - Payroll   : wt_month_settings + wt_salary_adjustments (محاسبهٔ deterministic، بدونِ LLM)

قواعد: mutation + audit اتمیک در یک تراکنش؛ audit در wt_hr_events (append-only)؛ هیچ تماسِ شبکه/LLM در محاسبه.
مبلغ‌ها عددِ صحیح در config.WT_SALARY_UNIT (پیش‌فرض toman)؛ تبدیل یک‌بار، در محاسبات مخلوط نمی‌شود.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time

import config
import db

_FA_NUM = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
SALARY_METHODS = {"fixed_monthly", "hourly"}
_MAG = {"میلیارد": 1_000_000_000, "میلیون": 1_000_000, "ملیون": 1_000_000, "هزار": 1_000}
# واژه‌های پرکننده که هنگامِ استخراجِ نامِ پرسنل حذف می‌شوند
_FILLER = {"رو", "را", "بکن", "کن", "بذار", "بزار", "بگذار", "ماهی", "ماهانه", "ثابت", "ساعتی",
           "تومان", "تومن", "ریال", "حقوق", "حقوقِ", "به", "برای", "بشه", "شود", "معادل"}


def _now() -> float:
    return time.time()


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _jdump(o):
    return None if o is None else json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fa(x) -> str:
    return str(x).translate(_EN_TO_FA)


# ---------- schema (additive، idempotent) ----------
def init_hr_schema() -> None:
    with db._lock:
        c = db._conn
        c.execute("""CREATE TABLE IF NOT EXISTS wt_personnel(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, tg_user_id INTEGER, title TEXT,
            active INTEGER NOT NULL DEFAULT 1, salary_amount INTEGER, salary_method TEXT,
            created_ts REAL, updated_ts REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wt_attendance(
            id INTEGER PRIMARY KEY AUTOINCREMENT, personnel_id INTEGER, tg_user_id INTEGER, work_date TEXT,
            event_time TEXT, event_ts REAL, kind TEXT, source TEXT, note TEXT, actor_id INTEGER,
            created_ts REAL, corrected INTEGER DEFAULT 0, dedupe_key TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wt_month_settings(
            month TEXT PRIMARY KEY, base_hours REAL, created_ts REAL, actor_id INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wt_salary_adjustments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, personnel_id INTEGER, month TEXT, final_override INTEGER,
            reason TEXT, actor_id INTEGER, created_ts REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wt_hr_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id INTEGER, event_type TEXT,
            actor_id INTEGER, prev_json TEXT, new_json TEXT, reason TEXT, occurred_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_personnel_tg ON wt_personnel(tg_user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_personnel_active ON wt_personnel(active)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_attendance_pm ON wt_attendance(personnel_id, work_date)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_attendance_tg ON wt_attendance(tg_user_id, work_date)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_wt_attendance_dedupe ON wt_attendance(dedupe_key) "
                  "WHERE dedupe_key IS NOT NULL AND dedupe_key<>''")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_wt_salary_adj ON wt_salary_adjustments(personnel_id, month)")
        # append-only audit
        c.execute("CREATE TRIGGER IF NOT EXISTS wt_hr_events_no_update BEFORE UPDATE ON wt_hr_events "
                  "BEGIN SELECT RAISE(ABORT, 'wt_hr_events is append-only'); END")
        c.execute("CREATE TRIGGER IF NOT EXISTS wt_hr_events_no_delete BEFORE DELETE ON wt_hr_events "
                  "BEGIN SELECT RAISE(ABORT, 'wt_hr_events is append-only'); END")
        c.commit()


def _audit(entity_type, entity_id, event_type, actor_id, prev=None, new=None, reason=None):
    db._conn.execute(
        "INSERT INTO wt_hr_events(entity_type, entity_id, event_type, actor_id, prev_json, new_json, reason, occurred_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (entity_type, entity_id, event_type, int(actor_id or 0), _jdump(prev), _jdump(new),
         (reason or None), _utc()))


# ============================================================
# Personnel service (نویسندهٔ یگانهٔ wt_personnel)
# ============================================================
def add_personnel(actor_id, name, tg_user_id=None, title="", salary_amount=None, salary_method=None) -> int:
    nm = (name or "").strip()
    if not nm:
        return -1
    if salary_method is not None and salary_method not in SALARY_METHODS:
        return -1
    now = _now()
    with db._lock:
        try:
            cur = db._conn.execute(
                "INSERT INTO wt_personnel(name, tg_user_id, title, active, salary_amount, salary_method, created_ts, updated_ts) "
                "VALUES (?,?,?,1,?,?,?,?)",
                (nm, int(tg_user_id) if tg_user_id else None, (title or "").strip() or None,
                 int(salary_amount) if salary_amount is not None else None, salary_method, now, now))
            pid = cur.lastrowid
            _audit("personnel", pid, "personnel_created", actor_id,
                   new={"name": nm, "tg_user_id": tg_user_id, "title": title, "active": 1})
            db._conn.commit()
            return pid
        except Exception:
            db._conn.rollback()
            raise


def edit_personnel(actor_id, pid, name=None, title=None, tg_user_id=None) -> bool:
    sets, vals, new = [], [], {}
    if name is not None and name.strip():
        sets.append("name=?"); vals.append(name.strip()); new["name"] = name.strip()
    if title is not None:
        sets.append("title=?"); vals.append(title.strip() or None); new["title"] = title.strip()
    if tg_user_id is not None:
        sets.append("tg_user_id=?"); vals.append(int(tg_user_id)); new["tg_user_id"] = int(tg_user_id)
    if not sets:
        return False
    sets.append("updated_ts=?"); vals.append(_now()); vals.append(int(pid))
    with db._lock:
        try:
            row = db._conn.execute("SELECT name, title, tg_user_id FROM wt_personnel WHERE id=?", (int(pid),)).fetchone()
            if not row:
                db._conn.rollback(); return False
            db._conn.execute(f"UPDATE wt_personnel SET {', '.join(sets)} WHERE id=?", vals)
            _audit("personnel", pid, "personnel_edited", actor_id,
                   prev={"name": row[0], "title": row[1], "tg_user_id": row[2]}, new=new)
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def set_active(actor_id, pid, active) -> bool:
    """غیرفعال/فعال‌سازی (نه حذفِ سخت). سابقه حفظ می‌شود؛ فعال‌سازیِ مجدد ممکن است."""
    a = 1 if active else 0
    with db._lock:
        try:
            row = db._conn.execute("SELECT active FROM wt_personnel WHERE id=?", (int(pid),)).fetchone()
            if not row:
                db._conn.rollback(); return False
            db._conn.execute("UPDATE wt_personnel SET active=?, updated_ts=? WHERE id=?", (a, _now(), int(pid)))
            _audit("personnel", pid, "personnel_activated" if a else "personnel_deactivated", actor_id,
                   prev={"active": row[0]}, new={"active": a})
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def set_salary(actor_id, pid, amount, method) -> bool:
    if method not in SALARY_METHODS or amount is None or int(amount) < 0:
        return False
    with db._lock:
        try:
            row = db._conn.execute("SELECT salary_amount, salary_method FROM wt_personnel WHERE id=?", (int(pid),)).fetchone()
            if not row:
                db._conn.rollback(); return False
            db._conn.execute("UPDATE wt_personnel SET salary_amount=?, salary_method=?, updated_ts=? WHERE id=?",
                             (int(amount), method, _now(), int(pid)))
            _audit("salary", pid, "salary_set", actor_id,
                   prev={"amount": row[0], "method": row[1]}, new={"amount": int(amount), "method": method})
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def get_personnel(pid):
    with db._lock:
        r = db._conn.execute(
            "SELECT id, name, tg_user_id, title, active, salary_amount, salary_method FROM wt_personnel WHERE id=?",
            (int(pid),)).fetchone()
    return _prow(r)


def _prow(r):
    if not r:
        return None
    return {"id": r[0], "name": r[1], "tg_user_id": r[2], "title": r[3], "active": r[4],
            "salary_amount": r[5], "salary_method": r[6]}


def list_personnel(active=None):
    q = "SELECT id, name, tg_user_id, title, active, salary_amount, salary_method FROM wt_personnel"
    args = ()
    if active is not None:
        q += " WHERE active=?"; args = (1 if active else 0,)
    q += " ORDER BY active DESC, name"
    with db._lock:
        return [_prow(r) for r in db._conn.execute(q, args).fetchall()]


def personnel_by_tg(tg_user_id):
    if not tg_user_id:
        return None
    with db._lock:
        r = db._conn.execute(
            "SELECT id, name, tg_user_id, title, active, salary_amount, salary_method FROM wt_personnel "
            "WHERE tg_user_id=? ORDER BY id LIMIT 1", (int(tg_user_id),)).fetchone()
    return _prow(r)


def find_personnel_by_name(hint):
    """پرسنلِ منطبق با نامِ تقریبی: فهرست (برای گاردِ ابهام). نامِ دقیق مقدم است."""
    h = (hint or "").strip().lower()
    if not h:
        return []
    out = []
    for p in list_personnel():
        nm = (p["name"] or "").lower()
        if nm == h:
            return [p]
        if h in nm or nm in h:
            out.append(p)
    return out


def is_active_personnel(tg_user_id):
    """True اگر پرسنلِ فعال، False اگر غیرفعال، None اگر پرسنل نیست (کاربرِ ناشناخته در حوزهٔ پرسنل)."""
    p = personnel_by_tg(tg_user_id)
    if not p:
        return None
    return bool(p["active"])


# ============================================================
# Attendance service (نویسندهٔ یگانهٔ wt_attendance)
# ============================================================
def _event_ts(work_date, event_time) -> float:
    """epochِ UTC از تاریخِ میلادیِ YYYY-MM-DD + ساعتِ HH:MM به‌وقتِ تهران."""
    try:
        y, mo, d = (int(x) for x in work_date.split("-")[:3])
        hh, mm = (int(x) for x in event_time.split(":")[:2])
        local = datetime.datetime(y, mo, d, hh, mm)
        return (local - datetime.timedelta(hours=3, minutes=30)).replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:  # noqa: BLE001
        return _now()


def record_event(actor_id, tg_user_id, work_date, event_time, kind, source="report", note="", event_ts=None):
    """یک رویدادِ حضور را با dedup ثبت می‌کند. personnel_id در صورتِ وجود resolve می‌شود. خروجی: id یا -1 (dup)."""
    if kind not in ("check_in", "check_out"):
        return -1
    p = personnel_by_tg(tg_user_id)
    pid = p["id"] if p else None
    ets = event_ts if event_ts is not None else _event_ts(work_date, event_time)
    dedupe = f"{tg_user_id}:{work_date}:{kind}:{event_time}:{source}"
    now = _now()
    with db._lock:
        try:
            cur = db._conn.execute(
                "INSERT INTO wt_attendance(personnel_id, tg_user_id, work_date, event_time, event_ts, kind, source, "
                "note, actor_id, created_ts, corrected, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
                (pid, int(tg_user_id) if tg_user_id else None, work_date, event_time, ets, kind, source,
                 (note or "").strip() or None, int(actor_id or 0), now, dedupe))
            aid = cur.lastrowid
            _audit("attendance", aid, "attendance_recorded", actor_id,
                   new={"tg": tg_user_id, "date": work_date, "time": event_time, "kind": kind, "source": source})
            db._conn.commit()
            return aid
        except sqlite3.IntegrityError:
            db._conn.rollback()
            return -1  # dedupe → ثبتِ تکراری دوباره محاسبه نمی‌شود
        except Exception:
            db._conn.rollback(); raise


def record_from_report(tg_user_id, work_date, check_in, check_out, actor_id=0):
    """مسیرِ موجود: از گزارشِ ورود/خروجِ روزانه دو رویدادِ حضور می‌سازد (بدونِ سیستمِ موازی)."""
    ci = record_event(actor_id, tg_user_id, work_date, check_in, "check_in", source="report")
    ci_ts = _event_ts(work_date, check_in)
    co_ts = _event_ts(work_date, check_out)
    if co_ts < ci_ts:                     # گذر از نیمه‌شب → خروج روزِ بعد
        co_ts += 24 * 3600
    co = record_event(actor_id, tg_user_id, work_date, check_out, "check_out", source="report", event_ts=co_ts)
    return (ci, co)


def manual_attendance(actor_id, tg_user_id, work_date, event_time, kind, reason):
    """اصلاح/افزودنِ دستیِ مدیر (source=manual) با دلیل + audit. حذفِ سخت انجام نمی‌شود."""
    if not (reason or "").strip():
        return -1
    return record_event(actor_id, tg_user_id, work_date, event_time, kind, source="manual",
                        note=f"correction: {reason.strip()}")


def void_attendance(actor_id, event_id, reason):
    """ابطالِ نرمِ یک رویدادِ اشتباه (corrected=1) با دلیل + audit — بدونِ حذفِ سخت."""
    if not (reason or "").strip():
        return False
    with db._lock:
        try:
            row = db._conn.execute("SELECT kind, work_date, event_time, corrected FROM wt_attendance WHERE id=?",
                                   (int(event_id),)).fetchone()
            if not row:
                db._conn.rollback(); return False
            db._conn.execute("UPDATE wt_attendance SET corrected=1 WHERE id=?", (int(event_id),))
            _audit("attendance", event_id, "attendance_voided", actor_id,
                   prev={"kind": row[0], "date": row[1], "time": row[2]}, reason=reason.strip())
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def jmonth_bounds(month):
    """«YYYY-MM»ِ شمسی → (g_start, g_end) میلادی به‌صورتِ YYYY-MM-DD. برای تجمیعِ ماهِ شمسی روی تاریخِ میلادی."""
    import jdatetime
    y, m = (int(x) for x in month.split("-")[:2])
    g_start = jdatetime.date(y, m, 1).togregorian()
    nxt = jdatetime.date(y + 1, 1, 1) if m == 12 else jdatetime.date(y, m + 1, 1)
    g_end = nxt.togregorian() - datetime.timedelta(days=1)
    return (g_start.strftime("%Y-%m-%d"), g_end.strftime("%Y-%m-%d"))


def current_jmonth() -> str:
    import clock
    import jdatetime
    j = jdatetime.date.fromgregorian(date=clock.tehran_now().date())
    return f"{j.year:04d}-{j.month:02d}"


def month_summary(tg_user_id, month):
    """جمعِ ساعاتِ معتبرِ ماهِ شمسی از جفت‌های ورود/خروج + شمارشِ ناقص/یتیم. month = «YYYY-MM» شمسی.

    الگوریتم: رویدادهای غیرِ corrected مرتب بر event_ts؛ هر check_in باز می‌شود و با check_outِ بعدی جفت می‌شود.
    """
    try:
        g_start, g_end = jmonth_bounds(month)
    except Exception:  # noqa: BLE001 — اگر ماه نامعتبر بود، بازهٔ خالی
        g_start = g_end = "0000-00-00"
    with db._lock:
        rows = db._conn.execute(
            "SELECT kind, event_ts, work_date, event_time FROM wt_attendance "
            "WHERE tg_user_id=? AND work_date>=? AND work_date<=? AND COALESCE(corrected,0)=0 ORDER BY event_ts, id",
            (int(tg_user_id), g_start, g_end)).fetchall()
    valid_min = 0.0
    open_in = None
    incomplete = 0
    orphan = 0
    days = set()
    first_in = last_out = None
    for kind, ets, wd, et in rows:
        days.add(wd)
        if kind == "check_in":
            if open_in is not None:
                incomplete += 1        # ورودِ قبلی بدونِ خروج ماند
            open_in = ets
            if first_in is None:
                first_in = f"{wd} {et}"
        else:  # check_out
            if open_in is not None:
                dur = (ets - open_in) / 60.0
                if dur > 0:
                    valid_min += dur
                open_in = None
                last_out = f"{wd} {et}"
            else:
                orphan += 1            # خروجِ بدونِ ورود
    if open_in is not None:
        incomplete += 1
    return {"valid_minutes": round(valid_min), "valid_hours": round(valid_min / 60.0, 2),
            "days": len(days), "incomplete": incomplete, "orphan": orphan,
            "first_in": first_in, "last_out": last_out, "events": len(rows)}


# ============================================================
# Payroll service (month settings + adjustments + محاسبهٔ deterministic)
# ============================================================
def set_month_base_hours(actor_id, month, base_hours) -> bool:
    try:
        bh = float(base_hours)
    except (TypeError, ValueError):
        return False
    if bh <= 0 or bh > 1000:
        return False
    with db._lock:
        try:
            prev = db._conn.execute("SELECT base_hours FROM wt_month_settings WHERE month=?", (month,)).fetchone()
            db._conn.execute(
                "INSERT INTO wt_month_settings(month, base_hours, created_ts, actor_id) VALUES (?,?,?,?) "
                "ON CONFLICT(month) DO UPDATE SET base_hours=excluded.base_hours, created_ts=excluded.created_ts, "
                "actor_id=excluded.actor_id", (month, bh, _now(), int(actor_id or 0)))
            _audit("month", 0, "month_base_hours_set", actor_id,
                   prev={"month": month, "base_hours": prev[0] if prev else None}, new={"month": month, "base_hours": bh})
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def get_month_base_hours(month):
    with db._lock:
        r = db._conn.execute("SELECT base_hours FROM wt_month_settings WHERE month=?", (month,)).fetchone()
    return r[0] if r else None


def set_final_adjustment(actor_id, personnel_id, month, final_override, reason) -> bool:
    """اصلاحِ مبلغِ نهاییِ ماه توسطِ مدیر با دلیل (تنها کسورات/تغییرِ مجاز در این نسخه)."""
    if final_override is None or int(final_override) < 0 or not (reason or "").strip():
        return False
    with db._lock:
        try:
            prev = db._conn.execute("SELECT final_override, reason FROM wt_salary_adjustments WHERE personnel_id=? AND month=?",
                                    (int(personnel_id), month)).fetchone()
            db._conn.execute(
                "INSERT INTO wt_salary_adjustments(personnel_id, month, final_override, reason, actor_id, created_ts) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(personnel_id, month) DO UPDATE SET "
                "final_override=excluded.final_override, reason=excluded.reason, actor_id=excluded.actor_id, "
                "created_ts=excluded.created_ts",
                (int(personnel_id), month, int(final_override), reason.strip(), int(actor_id or 0), _now()))
            _audit("salary", personnel_id, "salary_final_adjusted", actor_id,
                   prev={"final": prev[0] if prev else None}, new={"month": month, "final": int(final_override)}, reason=reason.strip())
            db._conn.commit(); return True
        except Exception:
            db._conn.rollback(); raise


def get_final_adjustment(personnel_id, month):
    with db._lock:
        r = db._conn.execute("SELECT final_override, reason FROM wt_salary_adjustments WHERE personnel_id=? AND month=?",
                             (int(personnel_id), month)).fetchone()
    return {"final_override": r[0], "reason": r[1]} if r else None


def compute_payroll(personnel_id, month) -> dict:
    """محاسبهٔ deterministic (بدونِ LLM). خروجی شاملِ روش، ساعت، مبنا، محاسبه، اصلاح، نهایی و وضعیتِ داده."""
    p = get_personnel(personnel_id)
    if not p:
        return {"ok": False, "reason": "personnel not found"}
    summ = month_summary(p["tg_user_id"], month) if p["tg_user_id"] else {"valid_hours": 0, "valid_minutes": 0, "incomplete": 0, "orphan": 0, "days": 0}
    base_hours = get_month_base_hours(month)
    adj = get_final_adjustment(personnel_id, month)
    method = p["salary_method"]
    amount = p["salary_amount"]
    hours = summ["valid_hours"]
    computed = None
    status = "ok"
    if method == "hourly":
        if amount is None:
            status = "no_rate"
        else:
            computed = int(round(hours * amount))
            if summ["events"] == 0 if "events" in summ else False:
                status = "no_attendance"
    elif method == "fixed_monthly":
        if amount is None:
            status = "no_amount"
        elif base_hours:
            computed = int(round(amount * (summ["valid_minutes"] / 60.0) / base_hours))
        else:
            computed = None                 # مبنای ماه ثبت نشده → محاسبهٔ تناسبی نکن
            status = "no_month_baseline"
    else:
        status = "no_method"
    if method == "hourly" and summ.get("valid_minutes", 0) == 0 and amount is not None:
        status = "no_attendance"
    final = adj["final_override"] if adj else (computed if computed is not None else None)
    return {"ok": True, "personnel": p, "month": month, "method": method, "amount": amount,
            "hours": hours, "valid_minutes": summ.get("valid_minutes", 0), "base_hours": base_hours,
            "days": summ.get("days", 0), "incomplete": summ.get("incomplete", 0), "orphan": summ.get("orphan", 0),
            "first_in": summ.get("first_in"), "last_out": summ.get("last_out"),
            "computed": computed, "adjustment": adj, "final": final, "status": status,
            "unit": config.WT_SALARY_UNIT}


# ============================================================
# Deterministic parsers (بدونِ LLM)
# ============================================================
def parse_money(text) -> int | None:
    """«۳۰ میلیون» → 30000000، «۲۰۰ هزار» → 200000، «۳۵٬۰۰۰٬۰۰۰» → 35000000. واحد = WT_SALARY_UNIT."""
    t = (text or "").translate(_FA_NUM).replace("٬", "").replace(",", "").replace("،", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(میلیارد|میلیون|ملیون|هزار)?", t)
    if not m:
        return None
    num = float(m.group(1))
    mult = _MAG.get(m.group(2), 1)
    val = int(round(num * mult))
    return val if val > 0 else None


def parse_salary_command(text) -> dict:
    """«حقوق علی ماهی ۳۰ میلیون تومان» → {ok, method, amount, name_hint}. نامِ نهایی توسطِ کالر با roster حل می‌شود."""
    t = (text or "").strip()
    if not t.startswith("حقوق"):
        return {"ok": False, "reason": "not_salary_command"}
    body = t[len("حقوق"):].strip()
    method = None
    if "ساعتی" in body:
        method = "hourly"
    elif "ماهی" in body or "ماهانه" in body or "ثابت" in body:
        method = "fixed_monthly"
    amount = parse_money(body)
    if amount is None:
        return {"ok": False, "reason": "no_amount"}
    # نامِ پرسنل = توکن‌های باقی‌مانده پس از حذفِ عدد/واحد/واژه‌های پرکننده
    toks = []
    for w in body.translate(_FA_NUM).split():
        wl = w.strip("،,.:؛")
        if not wl or wl in _FILLER:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", wl.replace("٬", "").replace(",", "")):
            continue
        if wl in _MAG:
            continue
        toks.append(wl)
    name_hint = " ".join(toks).strip()
    return {"ok": True, "method": method, "amount": amount, "name_hint": name_hint}


# ---------- نمایشِ پول ----------
_UNIT_FA = {"toman": "تومان", "rial": "ریال"}


def fmt_money(n) -> str:
    if n is None:
        return "—"
    s = f"{int(n):,}".replace(",", "٬")
    return f"{_fa(s)} {_UNIT_FA.get(config.WT_SALARY_UNIT, config.WT_SALARY_UNIT)}"


def method_fa(m) -> str:
    return {"fixed_monthly": "حقوقِ ثابتِ ماهانه", "hourly": "ساعتی"}.get(m, "—")
