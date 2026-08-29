"""ماژولِ «گزارشِ کارِ روزانه + ارزیابیِ عملکرد» — ایزوله، داخلِ همان باتِ woo-orderbot (دستیارِ مدیریتی).

فازِ ۱ (این فایل):
- گروهِ گزارشِ کار با /setworkgroup ثبت می‌شود.
- مدیر با منشنِ کاربر در آن گروه تسک می‌دهد → به تسک‌های آن کاربر افزوده می‌شود.
- کاربر با /tasks تسک‌های بازش را می‌بیند و با دکمه می‌بندد.
- کاربر گزارشِ روزانه می‌فرستد (/report یا پیامی که با «گزارش» شروع شود) → ذخیره می‌شود.
- پرسنل خودکار از فعالیتِ گروه کشف می‌شوند (wt_staff)؛ منشنِ @username از همین‌جا به آیدی نگاشت می‌شود.

فازِ بعد: ارزیابیِ AI (مغزِ ۵.۵) با سؤال‌وجواب + نمره، یادآوریِ الزامی، و تحلیلِ روندِ روزانه/ماهانه به مدیران (دایرکت).

جداولِ اختصاصی: wt_tasks / wt_reports / wt_staff. هیچ چیزی از منطقِ سفارش/CRM را تغییر نمی‌دهد.
"""
from __future__ import annotations

import asyncio
import html
import sqlite3
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import jdatetime

import clock
import config
import crm
import db
import igstats
import taskservice
import wt_brain
import wt_hr

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(_FA)


def _jalali(day_str) -> str:
    """«YYYY-MM-DD» میلادی → «YYYY/MM/DD» شمسی (ارقامِ فارسی)."""
    try:
        y, m, d = (int(x) for x in str(day_str).split("-")[:3])
        j = jdatetime.date.fromgregorian(year=y, month=m, day=d)
        return f"{_fa(j.year)}/{_fa('%02d' % j.month)}/{_fa('%02d' % j.day)}"
    except Exception:
        return str(day_str)


def _jalali_month(month_str) -> str:
    try:
        y, m = (int(x) for x in str(month_str).split("-")[:2])
        j = jdatetime.date.fromgregorian(year=y, month=m, day=1)
        return f"{_fa(j.year)}/{_fa('%02d' % j.month)}"
    except Exception:
        return str(month_str)


_awaiting: dict[int, float] = {}  # user_id → ts: منتظرِ متنِ گزارش پس از زدنِ دکمه
_AWAIT_TTL = 3600
_awaiting_block: dict[int, tuple] = {}  # user_id → (task_id, ts): منتظرِ دلیلِ مسدودشدن پس از زدنِ دکمهٔ «مسدود»


# ---------- راه‌اندازیِ جدول‌ها (روی همان اتصالِ db، بعد از db.init) ----------
def wt_init():
    with db._lock:
        db._conn.execute(
            """CREATE TABLE IF NOT EXISTS wt_tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                assignee_id   INTEGER,
                assignee_name TEXT,
                assigner_id   INTEGER,
                assigner_name TEXT,
                text          TEXT,
                status        TEXT DEFAULT 'open',
                created_ts    REAL,
                done_ts       REAL
            )"""
        )
        db._conn.execute(
            """CREATE TABLE IF NOT EXISTS wt_reports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                user_name  TEXT,
                day        TEXT,
                text       TEXT,
                created_ts REAL
            )"""
        )
        db._conn.execute(
            """CREATE TABLE IF NOT EXISTS wt_staff (
                user_id  INTEGER PRIMARY KEY,
                username TEXT,
                name     TEXT,
                first_ts REAL,
                last_ts  REAL
            )"""
        )
        db._conn.execute(
            """CREATE TABLE IF NOT EXISTS wt_directives (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scope        TEXT,
                target_id    INTEGER,
                text         TEXT,
                created_by   INTEGER,
                created_name TEXT,
                ts           REAL,
                active       INTEGER DEFAULT 1
            )"""
        )
        for col in ("ai_questions TEXT", "ai_answers TEXT", "ai_score INTEGER", "ai_summary TEXT",
                    "ai_flags TEXT", "ai_remaining TEXT", "ai_blockers TEXT", "ai_tasks TEXT", "kind TEXT",
                    "ai_carryover TEXT", "ai_growth TEXT",
                    "work_date TEXT", "check_in TEXT", "check_out TEXT", "worked_min INTEGER"):
            try:
                db._conn.execute(f"ALTER TABLE wt_reports ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        try:  # شرحِ وظایفِ هر پرسنل (برای اساینِ خودکارِ تسک‌های خزش)
            db._conn.execute("ALTER TABLE wt_staff ADD COLUMN role_desc TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # قفلِ نام: نامِ دستی (مثلاً فارسیِ درست) با پیام‌های بعدیِ تلگرام بازنویسی نشود
            db._conn.execute("ALTER TABLE wt_staff ADD COLUMN name_locked INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:  # کلیدِ دسته‌ی مشکلِ خزش روی تسک (برای جلوگیری از تسکِ تکراریِ همان مشکل)
            db._conn.execute("ALTER TABLE wt_tasks ADD COLUMN source_key TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # متریکِ مشکل (شمارش) برای تشخیصِ بدترشدن و رفرشِ تسک
            db._conn.execute("ALTER TABLE wt_tasks ADD COLUMN metric REAL")
        except sqlite3.OperationalError:
            pass
        try:  # حداکثر یک تسکِ بازِ خزش به‌ازای هر کلید (ضدِ ریسِ /crawl و خزشِ خودکار)
            db._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_wt_open_key ON wt_tasks(source_key) "
                "WHERE status='open' AND source_key IS NOT NULL AND source_key<>''")
        except sqlite3.OperationalError:
            pass
        db._conn.commit()
    taskservice.init_schema()  # جدول‌های audit + inbound-events + triggerهای append-only (additive)
    wt_hr.init_hr_schema()     # جدول‌های پرسنل/حضور/حقوق (additive، idempotent، append-only audit)
    print("[worktasks] جدول‌های گزارشِ کار آماده شد.")


def _workgroup() -> int:
    return int(db.get_meta("work_group") or 0)


def _is_admin(uid) -> bool:
    return uid in config.ADMIN_USER_IDS


# ---------- پرسنل (کشفِ خودکار) ----------
def _seen(user):
    """کاربر را در روسترِ پرسنل ثبت/به‌روز می‌کند (برای نگاشتِ @username→id و روند)."""
    if not user or getattr(user, "is_bot", False):
        return
    _seen_id(user.id, user.full_name, user.username)


def _seen_id(uid, name, username=None):
    now = time.time()
    with db._lock:
        db._conn.execute(
            """INSERT INTO wt_staff(user_id, username, name, first_ts, last_ts) VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username=COALESCE(excluded.username, wt_staff.username),
                   name=CASE WHEN COALESCE(wt_staff.name_locked,0)=1 THEN wt_staff.name ELSE excluded.name END,
                   last_ts=excluded.last_ts""",
            (uid, (username or "").lower() or None, name or str(uid), now, now),
        )
        db._conn.commit()


def _set_staff_name(uid, name):
    """نامِ نمایشیِ یک پرسنل را دستی تنظیم و «قفل» می‌کند تا با پیام‌های بعدی بازنویسی نشود."""
    with db._lock:
        db._conn.execute(
            "UPDATE wt_staff SET name=?, name_locked=1 WHERE user_id=?", ((name or "").strip(), int(uid)))
        db._conn.commit()


def _staff_by_username(username: str):
    u = (username or "").lstrip("@").lower()
    if not u:
        return None
    with db._lock:
        return db._conn.execute("SELECT user_id, name FROM wt_staff WHERE username=?", (u,)).fetchone()


def _staff_name(uid):
    with db._lock:
        r = db._conn.execute("SELECT name FROM wt_staff WHERE user_id=?", (int(uid),)).fetchone()
    return r[0] if r else None


# ---------- شرحِ وظایفِ پرسنل (برای اساینِ خودکارِ تسک‌های خزش) ----------
def _set_role(uid, text):
    with db._lock:
        db._conn.execute("UPDATE wt_staff SET role_desc=? WHERE user_id=?", ((text or "").strip(), int(uid)))
        db._conn.commit()


def _get_role(uid) -> str:
    with db._lock:
        r = db._conn.execute("SELECT role_desc FROM wt_staff WHERE user_id=?", (int(uid),)).fetchone()
    return (r[0] or "") if r else ""


def _staff_roles():
    """پرسنلِ دارای شرحِ وظایف: [(user_id, name, role_desc)]."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT user_id, name, role_desc FROM wt_staff WHERE role_desc IS NOT NULL AND role_desc!=''").fetchall()
    return [(u, n, d) for u, n, d in rows]


# ---------- تسک‌ها ----------
def _role_of(uid) -> str:
    """نقشِ واقعیِ actor از کد (نه LLM): primary_admin/admin/staff/system. سازگار با گذشته (primary فقط اگر config ست باشد)."""
    if not uid:
        return "system"
    if _is_admin(uid):
        pid = getattr(config, "WT_PRIMARY_ADMIN_ID", 0)
        return "primary_admin" if (pid and int(uid) == int(pid)) else "admin"
    return "staff"


def _mk_ctx(actor_id, operation, idem=""):
    """MutationContext برای عملیاتِ تسک: actor از assigner/telegram می‌آید (نه از LLM)؛ 0/خالی → system."""
    if not actor_id:
        return taskservice.system_context(operation, idempotency_key=idem)
    return taskservice.MutationContext(actor_id=int(actor_id), actor_role=_role_of(actor_id), source="telegram",
                                       operation=operation, idempotency_key=idem)


def _personnel_blocked(uid) -> bool:
    """پرسنلِ غیرفعال: تسکِ جدید نگیرد و عملیاتِ جدید نکند (فقط وقتی WT_PERSONNEL_ENABLED). ناشناخته → مسدود نیست."""
    if not getattr(config, "WT_PERSONNEL_ENABLED", False) or not uid:
        return False
    return wt_hr.is_active_personnel(uid) is False


def _is_retired(uid) -> bool:
    """قطعِ همکاریِ سبک (مستقل از flagِ HR): با metaِ retired:{uid}. بادوام و برگشت‌پذیر؛ سابقه حفظ می‌شود."""
    return bool(uid) and db.get_meta(f"retired:{int(uid)}") == "1"


def _staff_blocked(uid) -> bool:
    """پرسنلِ قطع‌همکاری‌شده یا غیرفعال: تسکِ جدید/عملیاتِ جدید ندارد."""
    return _is_retired(uid) or _personnel_blocked(uid)


def _set_retired(actor_id, uid, name, retire=True):
    """ثبتِ قطع/بازگشتِ همکاری + auditِ append-only (بدونِ حذفِ داده)."""
    with db._lock:
        db._conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (f"retired:{int(uid)}", "1" if retire else "0"))
        try:
            wt_hr._audit("staff", int(uid), "staff_retired" if retire else "staff_unretired",
                         actor_id, new={"name": name, "retired": bool(retire)})
        except Exception:  # noqa: BLE001 — audit نباید عملیات را بشکند
            pass
        db._conn.commit()


# اپراتور (اپراتور): سیستم برایش تسکِ خودکارِ خزش نمی‌سازد و «انجام شد»‌ش مستقیم بسته می‌شود —
# کارش دفترچه/اینستاست و با فروشِ ووکامرس قابلِ‌صحت‌سنجیِ خودکار نیست.
_OPERATOR_IDS = {i for i in (config.WT_MEDIAIMG_OPERATOR_ID, config.WT_CITIZEN_OPERATOR_ID) if i}


def _add_task(assignee_id, assignee_name, assigner_id, assigner_name, text, source_key=None, metric=None,
              ctx=None, kind="staff") -> int:
    """ساختِ تسک از طریقِ سرویسِ متمرکز (audit + idempotency + task_kind). قرارداد: id تسک، یا -1 برای dupِ source_key/رد."""
    if kind == "staff" and _staff_blocked(assignee_id):  # پرسنلِ قطع‌همکاری/غیرفعال تسکِ جدید نمی‌گیرد
        print(f"[worktasks] تسک به پرسنلِ قطع‌همکاری/غیرفعال ({assignee_id}) واگذار نشد.")
        return -1
    if int(assignee_id or 0) in _OPERATOR_IDS and str(assigner_name or "").startswith("🤖"):  # اپراتور تسکِ خودکار (🤖) نمی‌گیرد
        print(f"[worktasks] تسکِ خودکار برای اپراتور ({assignee_id}) ساخته نشد (مستثنیٰ).")
        return -1
    if ctx is None:
        ctx = _mk_ctx(assigner_id, "task_create")
    res = taskservice.create_task(ctx, assignee_id, assignee_name, assigner_name, text,
                                  source_key=source_key, metric=metric, task_kind=kind)
    if res.status == "applied":
        return res.task_id
    if res.status == "duplicate" and res.task_id is not None:  # retryِ همان idempotency-key → همان id
        return res.task_id
    return -1  # noop (source_key dup) / unauthorized / invalid


def _open_crawl_by_key() -> dict:
    """{key: {id, text, metric, created_ts, assignee_name}} برای تسک‌های بازِ خزش (dedup/رفرش/تشدید).

    D-04: مبنای سنِ تشدید = escalation_ref_ts (اگر NULL بود، fallbackِ legacy به created_ts). خودِ created_ts دیگر
    برای تشدید تغییر نمی‌کند؛ در همین dict با کلیدِ 'created_ts' مبنای تشدید برگردانده می‌شود (سازگاری با خواننده).
    """
    with db._lock:
        rows = db._conn.execute(
            "SELECT source_key, id, text, metric, COALESCE(escalation_ref_ts, created_ts), assignee_name "
            "FROM wt_tasks WHERE status='open' AND source_key IS NOT NULL AND source_key<>''"
        ).fetchall()
    return {r[0]: {"id": r[1], "text": r[2], "metric": r[3], "created_ts": r[4], "assignee_name": r[5]}
            for r in rows}


def _update_crawl_task(task_id, text, metric):
    """متن/متریکِ یک تسکِ بازِ خزش را از طریقِ سرویس (با audit) به‌روز می‌کند (D-05)."""
    taskservice.refresh_crawl_task(taskservice.system_context("crawl_refresh"), task_id, text, metric)


def _bump_crawl_task(task_id):
    """فاصله‌گذاریِ تشدید (D-04): escalation_ref_ts را به now می‌برد؛ created_ts دست‌نخورده می‌ماند. با audit."""
    taskservice.bump_crawl_escalation(taskservice.system_context("crawl_escalation_bump"), task_id)


def _recent_done_crawl_key(key, within_s) -> bool:
    """آیا همین کلید به‌تازگی (within_s ثانیه) done شده بود؟ (برای «دوباره ظاهر شد، حل نشده»)."""
    with db._lock:
        r = db._conn.execute(
            "SELECT 1 FROM wt_tasks WHERE source_key=? AND status='done' AND done_ts>=? LIMIT 1",
            (key, time.time() - within_s)).fetchone()
    return r is not None


def _words(s):
    for ch in "،—:/().,؛«»\"":
        s = (s or "").replace(ch, " ")
    return {w for w in s.split() if len(w) >= 3}


def _match_key(task_text, issues) -> str:
    """کلیدِ نزدیک‌ترین مشکل به متنِ تسکِ ساخته‌شده (بر اساسِ هم‌پوشانیِ واژه‌ها). خالی اگر پیدا نشد."""
    tw = _words(task_text)
    best, best_ov = "", 0
    for i in issues:
        ov = len(tw & _words(i.get("text")))
        if ov > best_ov:
            best, best_ov = i.get("key") or "", ov
    return best


_ROUTE_MIN_OVERLAP = 2  # حداقل هم‌پوشانیِ واژه با شرحِ وظایف برای اساینِ قطعیِ بدونِ LLM


def _deterministic_route(fresh, staff) -> dict:
    """مشکل→پرسنل را قطعی (بدونِ LLM) نگاشت می‌کند، فقط وقتی یک نفر به‌روشنی و به‌تنهایی مسئولش است.

    خروجی: {key: assignee_name}. مواردِ مبهم/چندمسئوله/بی‌match در خروجی نیستند → همان‌ها به route_issues (LLM) می‌روند.
    """
    routed = {}
    for i in fresh:
        key = i.get("key") or ""
        if not key:
            continue
        iw = _words(i.get("text"))
        scored = sorted(((len(iw & _words(d)), n) for _u, n, d in staff), reverse=True)
        if not scored:
            continue
        top_ov, top_name = scored[0]
        second_ov = scored[1][0] if len(scored) > 1 else 0
        if top_ov >= _ROUTE_MIN_OVERLAP and top_ov > second_ov:  # یکتا و مطمئن
            routed[key] = top_name
    return routed


def _open_tasks(user_id):
    with db._lock:
        return db._conn.execute(
            "SELECT id, text, assigner_name FROM wt_tasks WHERE assignee_id=? AND status='open' ORDER BY id",
            (user_id,),
        ).fetchall()


def _task_done(task_id, user_id, ctx=None) -> bool:
    """پرسنل تسکِ خودش را می‌بندد (مالکیت‌محور، از طریقِ سرویس). خروجی: True فقط اگر همین‌الان بسته شد."""
    if ctx is None:
        ctx = _mk_ctx(user_id, "task_mark_done")
    return taskservice.mark_done(ctx, task_id).status == "applied"


def _close_task_admin(tid, ctx=None):
    """مدیر/سیستم هر تسکِ بازی را می‌بندد (مالکیت‌محور نیست). خروجی: (assignee_name, text) یا None."""
    with db._lock:  # ردیف را برای پیامِ تأیید پیش از بستن بخوان
        r = db._conn.execute(
            "SELECT assignee_name, text FROM wt_tasks WHERE id=? AND status='open'", (int(tid),)).fetchone()
    if not r:
        return None
    if ctx is None:
        ctx = taskservice.system_context("task_mark_done")  # fail-safe: actorِ system (کالر معمولاً ctxِ مدیر می‌دهد)
    res = taskservice.mark_done(ctx, tid)
    return r if res.status in ("applied", "duplicate", "noop") else None


def _edit_task(tid, new_text, ctx=None):
    """متنِ یک تسکِ باز را با دستورِ مدیر اصلاح می‌کند (از طریقِ سرویس). خروجی: (assignee_name, old_text) یا None."""
    nt = (new_text or "").strip()
    if not nt:
        return None
    with db._lock:
        r = db._conn.execute(
            "SELECT assignee_name, text FROM wt_tasks WHERE id=? AND status='open'", (int(tid),)).fetchone()
    if not r:
        return None
    if ctx is None:
        ctx = taskservice.system_context("task_update")
    res = taskservice.update_task(ctx, tid, nt)
    return r if res.status in ("applied", "duplicate", "noop") else None


def _add_report(user_id, name, text, kind="work", attendance=None) -> int:
    a = attendance or {}
    # روزِ گزارش = تاریخی که کارمند در متن نوشته (work_date)، نه روزِ رسیدنِ پیام؛
    # چون گزارش معمولاً برای «دیروز» است و صبحِ روزِ بعد فرستاده می‌شود.
    day = a.get("work_date") or clock.tehran_now().strftime("%Y-%m-%d")
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO wt_reports(user_id, user_name, day, text, created_ts, kind, "
            "work_date, check_in, check_out, worked_min) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, name, day, text, time.time(), kind,
             a.get("work_date"), a.get("check_in"), a.get("check_out"), a.get("worked_min")),
        )
        db._conn.commit()
        return cur.lastrowid


_FA_NUM = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _parse_attendance(text):
    """ساعتِ ورود–خروج و تاریخِ کارکرد را از متنِ گزارش درمی‌آورد (فرمتِ اپراتور).

    خروجی: {"work_date","check_in","check_out","worked_min"} یا None اگر بازه‌ی «HH:MM - HH:MM» نبود.
    """
    import re
    t = (text or "").translate(_FA_NUM)
    m = re.search(r"(\d{1,2}):(\d{2})\s*(?:-|–|—|~|تا|ta)\s*(\d{1,2}):(\d{2})", t)
    if not m:
        return None
    h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and m1 < 60 and m2 < 60):
        return None
    worked = (h2 * 60 + m2) - (h1 * 60 + m1)
    if worked < 0:
        worked += 24 * 60  # شیفتِ گذر از نیمه‌شب (نادر)
    work_date = clock.tehran_now().strftime("%Y-%m-%d")  # پیش‌فرض: روزِ ثبت (اگر تاریخ در متن نبود)
    # تاریخِ شمسی را در هر دو ترتیب بشناس: «۱۴۰۵/۰۴/۲۱» (سال‌اول) و «۲۱/۴/۱۴۰۵» (روزاول). ماه همیشه وسط است.
    dm = re.search(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})", t)
    if dm:
        g1, mm, g3 = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        yy = dd = None
        if 1300 <= g1 <= 1499:      # YYYY/MM/DD
            yy, dd = g1, g3
        elif 1300 <= g3 <= 1499:    # DD/MM/YYYY
            yy, dd = g3, g1
        if yy and 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                work_date = jdatetime.date(yy, mm, dd).togregorian().strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001 — تاریخِ نامعتبر → همان روزِ ثبت
                pass
    return {"work_date": work_date, "check_in": f"{h1:02d}:{m1:02d}",
            "check_out": f"{h2:02d}:{m2:02d}", "worked_min": worked}


def _format_help_text() -> str:
    return (
        "🙏 مرسی که گزارش دادی! فقط یه نکتهٔ کوچیک تا زحماتت دقیق ثبت بشه — لطفاً اول <b>تاریخ</b> و "
        "<b>ساعتِ ورود–خروج</b> رو هم بنویس، بعد کارها. این‌طوری ساعاتِ کارکردت برای حقوق هم درست حساب می‌شه. "
        "درست مثلِ این 👇\n\n"
        "<code>شنبه ۱۴۰۵/۰۴/۲۰\n"
        "۱۰:۰۵ - ۱۸:۳۰\n"
        "- کارِ اول\n"
        "- کارِ دوم</code>\n\n"
        "دوباره با همین قالب بفرست، ممنونم 💚"
    )


def _leave_kind(text):
    """اگر گزارشِ کوتاه، اعلامِ «مرخصی» یا «تعطیل» باشد، نوعش را برمی‌گرداند؛ وگرنه None.

    گزارشِ بلند (کارِ واقعی که اتفاقاً واژه را دارد) تعطیل حساب نمی‌شود.
    """
    t = (text or "").strip()
    if not t or len(t) > 30:
        return None
    if "مرخص" in t:
        return "leave"
    if "تعطیل" in t or t.lower() in ("off", "day off"):
        return "holiday"
    return None


# ---------- دستورهای ماندگارِ مدیر (حلقه‌ی بازخورد) ----------
def _add_directive(scope, target_id, text, created_by, created_name) -> int:
    scope = "user" if scope == "user" else "global"
    tid = int(target_id) if (scope == "user" and target_id) else None
    with db._lock:
        cur = db._conn.execute(
            """INSERT INTO wt_directives(scope, target_id, text, created_by, created_name, ts, active)
               VALUES (?,?,?,?,?,?,1)""",
            (scope, tid, (text or "").strip(), created_by, created_name, time.time()))
        db._conn.commit()
        return cur.lastrowid


def _active_directives(user_id=None):
    """[(id, scope, target_id, text, created_name, ts)] — سراسری‌ها + (اگر uid) ویژه‌ی همان پرسنل."""
    with db._lock:
        if user_id is not None:
            return db._conn.execute(
                """SELECT id, scope, target_id, text, created_name, ts FROM wt_directives
                   WHERE active=1 AND (scope='global' OR (scope='user' AND target_id=?)) ORDER BY ts""",
                (int(user_id),)).fetchall()
        return db._conn.execute(
            """SELECT id, scope, target_id, text, created_name, ts FROM wt_directives
               WHERE active=1 AND scope='global' ORDER BY ts""").fetchall()


def _deactivate_directive(did) -> bool:
    with db._lock:
        cur = db._conn.execute("UPDATE wt_directives SET active=0 WHERE id=? AND active=1", (int(did),))
        db._conn.commit()
        return cur.rowcount > 0


def _format_directives(rows) -> str:
    if not rows:
        return ""
    out = []
    for did, scope, tgt, text, by, _ts in rows:
        tag = "سراسری" if scope == "global" else f"ویژه‌ی {html.escape(_staff_name(tgt) or str(tgt))}"
        out.append(f"• <code>#{did}</code> [{tag}] {html.escape(text)}  <i>(از {html.escape(by or '—')})</i>")
    return "\n".join(out)


def _directives_block(user_id=None) -> str:
    """بلوکِ «اولویتِ مطلق» برای تزریق به پرامپت‌ها. خالی اگر دستوری نباشد."""
    rows = _active_directives(user_id)
    if not rows:
        return ""
    lines = ["🔴 دستورهای مدیر (اولویتِ مطلق — همیشه و بی‌قیدوشرط رعایت کن):"]
    for i, (_did, scope, _tgt, text, _by, _ts) in enumerate(rows, 1):
        tag = "همه" if scope == "global" else "این پرسنل"
        lines.append(f"{_fa(i)}) [{tag}] {text}")
    return "\n".join(lines)


# ---------- ارزیابیِ AI (مغزِ ۵.۵): گزارش → سؤال → پاسخ → نمره ----------
_awaiting_answers: dict[int, int] = {}  # user_id → report_id (منتظرِ پاسخِ سؤالاتِ ارزیابی)


def _today_start() -> float:
    now = clock.tehran_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return time.time() - max(0.0, (now - start).total_seconds())


def _task_summaries(user_id):
    """تسک‌های انجام‌شده‌ی امروز + تسک‌های باز با سنِ عقب‌افتادگی (تا مغز پیگیریِ کارهای عقب‌مانده را بپرسد)."""
    start = _today_start()
    now = time.time()
    with db._lock:
        done = db._conn.execute(
            "SELECT text FROM wt_tasks WHERE assignee_id=? AND status='done' AND done_ts>=?", (user_id, start)).fetchall()
        opent = db._conn.execute(
            "SELECT text, created_ts FROM wt_tasks WHERE assignee_id=? AND status='open' ORDER BY created_ts",
            (user_id,)).fetchall()

    def _age(ts):
        d = int((now - float(ts or now)) // 86400)
        return f" ⏳عقب‌افتاده {_fa(d)} روز" if d >= 1 else ""
    done_s = "؛ ".join(r[0] for r in done) or "—"
    open_s = "؛ ".join(f"{r[0]}{_age(r[1])}" for r in opent) or "—"
    return (done_s, open_s)


def _carryover_context(user_id) -> str:
    """کارِ مانده‌ی گزارشِ قبلی + مدارکِ رفع/عدمِ‌رفع، برای راستی‌آزماییِ صریحِ مغز."""
    import datetime
    today = clock.tehran_now().strftime("%Y-%m-%d")
    start = _today_start()
    wk = (clock.tehran_now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    with db._lock:
        prev = db._conn.execute(
            "SELECT day, ai_remaining, ai_blockers FROM wt_reports "
            "WHERE user_id=? AND day<? AND ai_summary IS NOT NULL "
            "ORDER BY day DESC, id DESC LIMIT 1", (user_id, today)).fetchone()
        resolved = db._conn.execute(
            "SELECT text FROM wt_tasks WHERE assignee_id=? AND status='done' "
            "AND done_ts>=? AND created_ts<?", (user_id, start, start)).fetchall()
        hist = db._conn.execute(
            "SELECT ai_remaining FROM wt_reports WHERE user_id=? AND day>=? AND day<? "
            "AND ai_remaining IS NOT NULL AND ai_remaining!='' ORDER BY day DESC", (user_id, wk, today)).fetchall()
    if not prev and not resolved:
        return ""
    parts = []
    if prev:
        d, rem, blk = prev
        if rem:
            parts.append(f"کارِ مانده‌ی گزارشِ قبلی ({_jalali(d)}) که امروز باید صریح راستی‌آزمایی شود: {rem.replace(' | ', '؛ ')}")
        if blk:
            parts.append(f"موانعی که آن روز اعلام شد (بپرس رفع شد یا نه): {blk.replace(' | ', '؛ ')}")
    if resolved:
        parts.append("کارهای کهنه‌ای که امروز بالاخره بسته شد (به این‌ها امتیازِ مثبت بده): "
                     + "؛ ".join(r[0] for r in resolved))
    if len(hist) >= 3:
        parts.append(f"هشدار: این پرسنل در {_fa(len(hist))} روزِ اخیر مکرراً کارِ مانده داشته — "
                     "عقب‌افتادگیِ تکرارشونده را در صورتِ تأیید در flags پرچم بزن.")
    return "🔁 راستی‌آزماییِ کارِ مانده:\n" + "\n".join(parts)


def _report_by_id(rid):
    with db._lock:
        r = db._conn.execute(
            "SELECT id, user_id, user_name, text, ai_questions FROM wt_reports WHERE id=?", (rid,)).fetchone()
    keys = ("id", "user_id", "user_name", "text", "ai_questions")
    return dict(zip(keys, r)) if r else {}


def _store_report_field(rid, field, val):
    if field not in ("ai_questions", "ai_answers"):  # whitelist
        return
    with db._lock:
        db._conn.execute(f"UPDATE wt_reports SET {field}=? WHERE id=?", (val, rid))
        db._conn.commit()


_CO_ICON = {"done": "✅", "partial": "🟡", "open": "❌", "unknown": "❔"}


def _store_eval(rid, ev):
    tasks_s = " | ".join(t["label"] for t in (ev.get("tasks") or []) if isinstance(t, dict) and t.get("label"))
    carry_s = " | ".join(
        f"{_CO_ICON.get(c.get('status'), '❔')}{'🔁' if c.get('recurring') else ''} {c.get('item', '')}"
        + (f" — {c['detail']}" if c.get('detail') else "")
        for c in (ev.get("carryover") or []) if isinstance(c, dict) and c.get("item"))
    with db._lock:
        db._conn.execute(
            "UPDATE wt_reports SET ai_score=?, ai_summary=?, ai_flags=?, ai_remaining=?, ai_blockers=?, "
            "ai_tasks=?, ai_carryover=?, ai_growth=? WHERE id=?",
            (ev.get("score"), ev.get("summary", ""), " | ".join(ev.get("flags") or []),
             " | ".join(ev.get("remaining") or []), " | ".join(ev.get("blockers") or []),
             tasks_s, carry_s, " | ".join(ev.get("growth_tips") or []), rid))
        db._conn.commit()


_store_cache: dict = {"t": 0.0, "v": ""}  # کشِ ۱۵دقیقه‌ایِ آمارِ فروشگاه (کراس‌چکِ ادعاها)


async def _store_context() -> str:
    """عکس‌فوریِ آمارِ واقعیِ ووکامرس تا مغز، ادعاهای عددیِ کارمند را صحت‌سنجی کند.

    مثال: کارمند می‌گوید «۱۰۰ محصول دسته‌بندی شد» → مغز کلِ محصولات و
    محصولاتِ دسته‌بندی‌نشده را می‌بیند و ناسازگاری را می‌فهمد. fail-soft.
    """
    now = time.time()
    if _store_cache["v"] and now - _store_cache["t"] < 900:
        return _store_cache["v"]
    out = ""
    try:
        import woo
        total = await woo.total_count("products", {"status": "publish"})
        ncats = await woo.total_count("products/categories", {})
        uncat = None
        try:
            ul = await woo.get("products/categories", {"slug": "uncategorized", "_fields": "count"})
            if ul:
                uncat = int(ul[0].get("count") or 0)
        except Exception:
            pass
        parts = [f"کلِ محصولاتِ منتشرشده={_fa(total)}", f"تعدادِ دسته‌بندی‌ها={_fa(ncats)}"]
        if uncat is not None:
            parts.append(f"محصولاتِ دسته‌بندی‌نشده={_fa(uncat)}")
        out = "آمارِ واقعیِ فروشگاه (ووکامرس) برای صحت‌سنجیِ ادعاها: " + "، ".join(parts)
        _store_cache["t"] = now
        _store_cache["v"] = out
    except Exception as e:
        print(f"[worktasks] store_context خطا: {e!r}")
    return out


def _ig_admin_uid() -> int:
    try:
        return int(db.get_meta("ig_admin_uid") or 0)
    except (TypeError, ValueError):
        return 0


def _wp_link(uid) -> int | None:
    v = db.get_meta(f"wp_link:{uid}")
    try:
        return int(v) if v else None
    except (TypeError, ValueError):
        return None


_WP_ACTION_FA = {
    "product_created": "ساختِ محصول", "product_updated": "ویرایشِ محصول",
    "product_categorized": "دسته‌بندی", "product_tagged": "برچسب", "price_changed": "قیمت‌گذاری",
    "stock_changed": "موجودی", "image_changed": "عکس", "seo_updated": "سئو",
    "product_status_changed": "انتشار/وضعیتِ محصول", "product_deleted": "حذفِ محصول",
    "order_status_changed": "وضعیتِ سفارش", "order_note_added": "یادداشتِ سفارش",
    "order_edited": "ویرایشِ سفارش", "order_refunded": "بازگشتِ وجه",
    "coupon_created": "ساختِ کوپن", "coupon_updated": "ویرایشِ کوپن",
    "user_created": "ساختِ کاربر", "user_updated": "ویرایشِ کاربر",
    "lead_status": "وضعیتِ لید", "lead_note": "یادداشتِ لید", "lead_assigned": "اساینِ لید",
    "content_published": "انتشارِ محتوا", "content_updated": "ویرایشِ محتوا",
    "media_uploaded": "آپلودِ مدیا", "review_status_changed": "نظر/ری‌ویو",
    "login": "ورود", "logout": "خروج",
}


async def _staff_context(user_id) -> str:
    """کارِ واقعیِ همین پرسنل برای صحت‌سنجی: شرحِ وظایف + آنالیزِ اینستاگرام (اگر ادمینِ پیج) + فعالیتِ سایت (اگر لینک)."""
    parts = []
    role = _get_role(user_id)
    if role:
        parts.append(f"شرحِ وظایفِ این پرسنل (عملکردش را نسبت به این بسنج): {role}")
    if user_id and user_id == _ig_admin_uid():
        try:
            r = await igstats.summary()
            fl = igstats.facts_line(r)
            if fl:
                parts.append(fl)
                bc = r.get("brand_coverage") or {}
                if bc:
                    parts.append("پوششِ برندِ اخیرِ پیج: " + "، ".join(f"{b}×{c}" for b, c in list(bc.items())[:6]))
                recs = r.get("recommendations") or []
                if recs:
                    parts.append("توصیه‌های آنالیزورِ محتوا برای این ادمین (در نمره‌دهی و تسک لحاظ کن): "
                                 + "؛ ".join(x["text"] for x in recs[:4]))
        except Exception as e:  # noqa: BLE001
            print(f"[worktasks] ig staff-context خطا: {e!r}")
    wp = _wp_link(user_id)
    if wp:
        try:
            import datetime
            now = clock.tehran_now()
            # پنجره‌ی ۲روزه: شیفت ممکن است از نیمه‌شب رد شود یا گزارش کمی بعد از نیمه‌شب بیاید
            frm = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            to = now.strftime("%Y-%m-%d")
            a = await crm.activity(wp, frm, to)
            if a.get("ok"):
                counts = a.get("counts") or {}
                if counts:
                    cs = "، ".join(f"{_WP_ACTION_FA.get(k, k)}={v}" for k, v in counts.items())
                    parts.append(f"کارِ واقعیِ ثبت‌شده در سایت (۲ روزِ اخیر، کاربرِ {a.get('user_login', '')}): {cs} (کل={a.get('total', 0)})")
                else:
                    parts.append(f"کارِ ثبت‌شده در سایت (۲ روزِ اخیر) برای کاربرِ {a.get('user_login', '')}: هیچ موردی (۰)")
        except Exception as e:  # noqa: BLE001
            print(f"[worktasks] wp staff-context خطا: {e!r}")
    return "؛ ".join(parts)


async def _process_report(msg, user, text) -> None:
    """گزارش را ذخیره می‌کند، تشکر می‌کند، و اگر مغز فعال بود سؤالِ پیگیرانه می‌پرسد.

    اگر گزارش «مرخصی/تعطیل» باشد، فقط ثبت و تأیید می‌شود (بدونِ سؤال/صحت‌سنجی/تسک/نمره).
    """
    nm = _staff_name(user.id) or user.full_name  # نامِ نمایشیِ فارسیِ ثبت‌شده (اگر قفل شده باشد)
    kind = _leave_kind(text)
    if kind:
        _add_report(user.id, nm, text, kind=kind)
        if kind == "leave":
            await msg.reply_text("🌴 مرخصیت ثبت شد؛ حسابی استراحت کن و انرژی بگیر 💚 "
                                 "امروز خیالت راحت — ارزیابی و تسکی نداری. 🌷")
        else:
            await msg.reply_text("📴 امروز تعطیله و ثبت شد؛ حسابی به خودت برس و لذت ببر 😊 "
                                 "(امروز ارزیابی و تسکی نداری.)")
        await maybe_send_perf_when_complete(msg.get_bot())
        return
    att = _parse_attendance(text)
    if not att:  # فرمتِ اشتباه (بدونِ ساعتِ ورود–خروج) → از نو با فرمتِ درست بفرستد
        await msg.reply_text(_format_help_text(), parse_mode=ParseMode.HTML)
        return
    rid = _add_report(user.id, nm, text, attendance=att)
    if getattr(config, "WT_ATTENDANCE_ENABLED", False):  # همان مسیرِ گزارش → دو رویدادِ حضور (dedup‌شده)
        try:
            wt_hr.record_from_report(user.id, att["work_date"], att["check_in"], att["check_out"])
        except Exception as e:  # noqa: BLE001 — ثبتِ حضور نباید مسیرِ گزارش را بشکند
            print(f"[worktasks] attendance record خطا: {e!r}")
    h, mnt = att["worked_min"] // 60, att["worked_min"] % 60
    await msg.reply_text(
        f"🌟 دمت گرم، گزارشت ثبت شد!\n"
        f"🕒 ورود {_fa(att['check_in'])} · خروج {_fa(att['check_out'])} · کارکرد {_fa(f'{h}:{mnt:02d}')}\n"
        f"ممنون بابتِ زحمتی که امروز کشیدی 🙏💚")
    if wt_brain.enabled():
        asyncio.create_task(_ai_followup(msg, user, rid, text))
    else:
        await maybe_send_perf_when_complete(msg.get_bot())


_followup_inflight: set = set()  # ridهایی که همین حالا در حالِ تولیدِ سؤال‌اند (ضدِ دوبار‌پرسیِ live/resume)


def _followup_asked(rid) -> bool:
    return db.get_meta(f"followup_asked:{rid}") == "1"


def _mark_followup_asked(rid):
    db.set_meta(f"followup_asked:{rid}", "1")


# ---------- snapshotِ context برای یک چرخهٔ گزارش (followup + evaluate) ----------
# بخشِ شبکه‌ایِ context (staff_context = آنالیزِ IG/فعالیتِ WP) در یک چرخه فقط یک‌بار fetch می‌شود و به هر دو
# مصرف‌کننده تزریق می‌گردد. کشِ کوتاه‌مدت per-rid (نه بلندمدت/stale). _store_context خودش کشِ ۱۵دقیقه دارد.
_report_ctx: dict[int, tuple[str, float]] = {}   # rid → (staff_context, ts)
_REPORT_CTX_TTL = 6 * 3600                        # پنجرهٔ یک چرخهٔ گزارش→پاسخ


def _ctx_log(rid, kind, size):
    try:  # فقط متریک؛ هیچ محتوایی لاگ نمی‌شود
        print(f"[ctx] rid={rid} {kind} staff_ctx_chars={size}")
    except Exception:  # noqa: BLE001
        pass


async def _staff_context_cycle(rid, user_id) -> str:
    """staff_context را برای یک چرخهٔ گزارش یک‌بار می‌سازد و برای evaluate بازاستفاده می‌کند (fail-soft)."""
    snap = _report_ctx.get(rid)
    if snap and (time.time() - snap[1]) < _REPORT_CTX_TTL:
        _ctx_log(rid, "reuse", len(snap[0] or ""))
        return snap[0]
    sc = await _staff_context(user_id)            # همان تابعِ فعلی؛ تنها اینجا fetchِ شبکه رخ می‌دهد
    cutoff = time.time() - _REPORT_CTX_TTL         # پاک‌سازیِ سبکِ ورودی‌های کهنه (ضدِ نشتِ حافظه)
    for k in [k for k, (_v, ts) in _report_ctx.items() if ts < cutoff]:
        _report_ctx.pop(k, None)
    _report_ctx[rid] = (sc, time.time())
    _ctx_log(rid, "build", len(sc or ""))
    return sc


async def _gen_followup_questions(user_id, user_name, report_text, staff_ctx=None) -> str:
    """کانتکستِ کامل (تسک‌ها + آمارِ فروشگاه + کارِ مانده + دستورهای مدیر) را می‌سازد و سؤال‌های پیگیری را می‌گیرد.

    staff_ctx (اختیاری): اگر از snapshotِ چرخه داده شود، staff_context دوباره از شبکه گرفته نمی‌شود.
    """
    done, opent = _task_summaries(user_id)
    store = await _store_context()
    sc = staff_ctx if staff_ctx is not None else await _staff_context(user_id)
    if sc:
        store = (store + "\n" + sc) if store else sc
    co = _carryover_context(user_id)
    if co:
        store = (store + "\n" + co) if store else co
    directives = _directives_block(user_id)
    return (await wt_brain.followup_questions(user_name, done, opent, report_text, store, directives)).strip()


async def _ai_followup(msg, user, rid, report_text):
    """مسیرِ زنده: بلافاصله پس از گزارش، سؤالِ پیگیری را همان‌جا (ریپلای) می‌پرسد."""
    if rid in _followup_inflight:
        return
    _followup_inflight.add(rid)
    try:
        sc = await _staff_context_cycle(rid, user.id)   # snapshotِ چرخه (یک‌بار fetch؛ برای evaluate بازاستفاده)
        qs = await _gen_followup_questions(user.id, _staff_name(user.id) or user.full_name, report_text, staff_ctx=sc)
        if qs:
            _awaiting_answers[user.id] = rid
            _store_report_field(rid, "ai_questions", qs)
            await msg.reply_text(
                f"🤖 مرسی از گزارشت! برای اینکه زحماتت کامل و درست دیده بشه، لطفاً کوتاه به این‌ها جواب بده 👇\n\n{qs}")
        else:  # سؤالی نبود → گزارش همین‌جا تمام است
            await maybe_send_perf_when_complete(msg.get_bot())
        _mark_followup_asked(rid)
    except Exception as e:
        print(f"[worktasks] ai_followup خطا: {e!r}")
    finally:
        _followup_inflight.discard(rid)


async def _resume_followup(bot, rid, user_id, user_name, report_text):
    """مسیرِ جبرانی: سؤالِ پیگیریِ یک گزارشِ جامانده (که ری‌استارت/کرش وسطش افتاد) را در گروهِ کار با منشن می‌پرسد."""
    group = _workgroup()
    if not group or rid in _followup_inflight or user_id in _awaiting_answers:
        return  # بدونِ گروه، پاسخِ کارمند قابلِ ثبت نیست (هندلرِ گروه آن را می‌گیرد)
    _followup_inflight.add(rid)
    try:
        sc = await _staff_context_cycle(rid, user_id)   # snapshotِ چرخه (یک‌بار fetch؛ برای evaluate بازاستفاده)
        qs = await _gen_followup_questions(user_id, user_name, report_text, staff_ctx=sc)
        if qs:
            _awaiting_answers[user_id] = rid
            _store_report_field(rid, "ai_questions", qs)
            mention = f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
            await bot.send_message(
                group,
                f"{mention} 🤖 مرسی از گزارشت! چند سؤالِ کوتاه مونده که زحماتت کامل دیده بشه — "
                f"لطفاً همین‌جا جواب بده 👇\n\n{qs}", parse_mode=ParseMode.HTML)
        else:  # سؤالی نبود → گزارش تمام است
            await maybe_send_perf_when_complete(bot)
        _mark_followup_asked(rid)
        print(f"[worktasks] سؤالِ پیگیریِ جامانده برای گزارشِ {rid} ({user_name}) پرسیده شد.")
    except Exception as e:
        print(f"[worktasks] resume_followup خطا ({rid}): {e!r}")
    finally:
        _followup_inflight.discard(rid)


async def maybe_resume_followups(app):
    """خوددرمان: گزارش‌های کاریِ اخیری که سؤالِ پیگیری‌شان (به‌خاطرِ ری‌استارت/کرش) نپرسیده مانده را جبران می‌کند."""
    if not wt_brain.enabled():
        return
    cutoff = time.time() - 2 * 24 * 3600  # فقط ۲ روزِ اخیر (نه رستاخیزِ گزارش‌های کهنه)
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, user_id, user_name, text FROM wt_reports "
            "WHERE kind='work' AND COALESCE(ai_questions,'')='' AND COALESCE(ai_score,'')='' "
            "AND created_ts>=? ORDER BY id", (cutoff,)).fetchall()
    for rid, uid, uname, text in rows:
        if rid in _followup_inflight or uid in _awaiting_answers or _followup_asked(rid):
            continue
        await _resume_followup(app.bot, rid, uid, uname, text)
        await asyncio.sleep(0.5)


async def _finalize_eval(msg, user, rid, answers):
    _store_report_field(rid, "ai_answers", answers)
    made = 0
    try:
        rep = _report_by_id(rid)
        done, opent = _task_summaries(user.id)
        qa = f"{rep.get('ai_questions', '')}\nپاسخِ کارمند: {answers}"
        store = await _store_context()
        sc = await _staff_context_cycle(rid, user.id)   # از snapshotِ همان چرخه (بدونِ fetchِ دوبارهٔ IG/WP)
        if sc:
            store = (store + "\n" + sc) if store else sc
        co = _carryover_context(user.id)
        if co:
            store = (store + "\n" + co) if store else co
        directives = _directives_block(user.id)
        nm = _staff_name(user.id) or user.full_name  # نامِ فارسیِ ثبت‌شده برای مغز و تسک‌ها
        ev = await wt_brain.evaluate(nm, done, opent, rep.get("text", ""), qa, store, directives)
        if ev:
            _store_eval(rid, ev)
            for t in sorted(ev.get("tasks") or [],
                            key=lambda x: {"high": 0, "med": 1, "low": 2}.get(x.get("priority"), 1))[:6]:
                _add_task(user.id, nm, 0, "🤖 مدیرِ داخلی", t["label"])
                made += 1
    except Exception as e:
        print(f"[worktasks] finalize خطا: {e!r}")
    _report_ctx.pop(rid, None)  # پایانِ چرخه → snapshot آزاد شود
    tail = (f"\n📌 برای فردا {_fa(made)} تسکِ کوچک برات گذاشتم که مسیرت روشن باشه (با /tasks ببین). 💪"
            if made else "")
    await msg.reply_text("✅ عالی بود، ثبت شد! زحماتت برای مدیر لحاظ و دیده شد. دمت گرم 🙌" + tail)
    await maybe_send_perf_when_complete(msg.get_bot())


def _mentioned_users(msg):
    """کاربرانِ منشن‌شده: text_mention (id مستقیم) + @username (از wt_staff). خروجی [(id, name), …]."""
    out = {}
    txt = msg.text or ""
    for ent in (msg.entities or []):
        if ent.type == "text_mention" and ent.user and not ent.user.is_bot:
            out[ent.user.id] = ent.user.full_name
        elif ent.type == "mention":
            uname = txt[ent.offset:ent.offset + ent.length]
            r = _staff_by_username(uname)
            if r:
                out[r[0]] = r[1]
    return list(out.items())


def _staff_by_name(hint):
    """پرسنل با نام/یوزرنیمِ تقریبی: (user_id, name) یا None."""
    h = (hint or "").strip().lstrip("@").lower()
    if not h:
        return None
    with db._lock:
        rows = db._conn.execute("SELECT user_id, name, username FROM wt_staff").fetchall()
    for uid, name, uname in rows:
        if uname and uname == h:
            return (uid, name)
    for uid, name, uname in rows:
        if name and h in name.lower():
            return (uid, name)
    return None


def _resolve_target(msg, hint):
    """هدفِ دستور/تسکِ شخصی: منشن → زنجیره‌ی ریپلای (پیامِ ربات که خودش ریپلای به پرسنل بوده) → hintِ AI."""
    ms = _mentioned_users(msg)
    if ms:
        return ms[0]
    r = msg.reply_to_message
    if r and r.reply_to_message and r.reply_to_message.from_user and not r.reply_to_message.from_user.is_bot:
        t = r.reply_to_message.from_user
        return (t.id, t.full_name)
    return _staff_by_name(hint)


def _name_matches(hint) -> int:
    """تعدادِ پرسنلِ منطبق با نامِ تقریبی (برای گاردِ ابهام). یوزرنیمِ دقیق = یکتا (۱)."""
    h = (hint or "").strip().lstrip("@").lower()
    if not h:
        return 0
    with db._lock:
        rows = db._conn.execute("SELECT name, username FROM wt_staff").fetchall()
    if any(u and u.lower() == h for _n, u in rows):
        return 1
    return sum(1 for n, _u in rows if n and h in n.lower())


async def _handle_manager_reply(msg, user, ev_id=0) -> None:
    """ریپلای مدیر روی پیامِ ربات را تفسیر و اجرا می‌کند: directive، ساخت/بستنِ تسک، اصلاح، «چشم مدیر».

    idempotency: تحویلِ دوبارهٔ همین update دوباره اعمال نمی‌شود (claim ورودی + کلیدِ idempotency برای هر mutation).
    ضدِ ابهام: تسکِ hallucinated یا نامِ چندمعنا mutation نمی‌سازد (lookup در سرویس + گاردِ نام).
    """
    original = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    reply = (msg.text or "").strip()

    ctx_parts = []
    with db._lock:
        roster = db._conn.execute("SELECT name, username FROM wt_staff ORDER BY last_ts DESC LIMIT 30").fetchall()
    if roster:
        ctx_parts.append("اعضای تیم: " + "، ".join(f"{n}" + (f" (@{u})" if u else "") for n, u in roster))
    likely = _resolve_target(msg, "")
    if likely:
        rows = _open_tasks(likely[0])
        if rows:
            ctx_parts.append(f"تسک‌های بازِ {likely[1]}: " + "؛ ".join(f"#{tid} {t}" for tid, t, _a in rows))
    ctx_txt = "\n".join(ctx_parts)

    r = await wt_brain.interpret_manager_reply(original, reply, ctx_txt)
    if not r:  # خروجیِ مبهم/خالیِ LLM → هیچ mutationی اجرا نشود (fail closed)
        await msg.reply_text("چشم مدیر، متوجه شدم. (تفسیرِ خودکار خطا داد؛ اگر دستورِ دائمی است، کوتاه و صریح دوباره بفرست.)")
        return

    # idempotencyِ ورودی توسطِ wrapperِ on_group_message انجام می‌شود (claimِ سراسری)؛ اینجا فقط کلیدِ سطحِ عملیات.
    def _mctx(op, suffix):  # ctxِ مدیر برای هر mutation — actor/role از کد، نه از LLM
        return taskservice.MutationContext(actor_id=user.id, actor_role=_role_of(user.id), source="telegram",
                                           operation=op, source_event_id=str(ev_id),
                                           idempotency_key=f"telegram:{ev_id}:{op}:{suffix}")

    if True:
        done_lines = []
        explicit = bool(_mentioned_users(msg)) or bool(
            msg.reply_to_message and msg.reply_to_message.reply_to_message)
        target = _resolve_target(msg, r["target_hint"]) if r["scope"] == "user" else None

        if r["directive"]:
            if r["scope"] == "user" and target:
                _add_directive("user", target[0], r["directive"], user.id, user.full_name)
                done_lines.append(f"📌 دستورِ دائمی برای «{html.escape(target[1])}» ثبت شد و از این پس رعایت می‌شود.")
            else:
                _add_directive("global", None, r["directive"], user.id, user.full_name)
                done_lines.append("📌 دستورِ دائمی برای کلِ تیم ثبت شد و در ارزیابی‌های بعدی اعمال می‌شود.")

        if r["tasks"]:
            ambiguous = (not explicit) and r["scope"] == "user" and _name_matches(r["target_hint"]) > 1
            if target and not ambiguous:
                _seen_id(target[0], target[1])
                for idx, t in enumerate(r["tasks"][:6]):
                    _add_task(target[0], target[1], user.id, user.full_name, t,
                              ctx=_mctx("task_create", f"{idx}:{target[0]}"))
                done_lines.append(f"🗂️ {_fa(len(r['tasks'][:6]))} تسک برای «{html.escape(target[1])}» ساخته شد.")
            elif ambiguous:
                done_lines.append("⚠️ چند نفر با این نام هست؛ برای جلوگیری از اشتباه تسک ساخته نشد — منشنش کن یا روی پیامش ریپلای بزن.")
            else:
                done_lines.append("⚠️ تسک ساخته نشد چون پرسنلِ هدف مشخص نبود — روی پیامِ خودِ او ریپلای بزن یا منشنش کن.")

        for e in r.get("edits", []):
            rr = _edit_task(e["task_id"], e["new_text"], ctx=_mctx("task_update", e["task_id"]))
            done_lines.append(
                f"✏️ تسک #{_fa(e['task_id'])} اصلاح شد → «{html.escape(e['new_text'][:70])}»" if rr
                else f"↪️ تسک #{_fa(e['task_id'])} برای اصلاح پیدا نشد (شاید بسته است).")

        for tid in r["close_task_ids"]:
            rr = _close_task_admin(tid, ctx=_mctx("task_mark_done", tid))
            done_lines.append(f"✅ تسک #{_fa(tid)} («{html.escape(rr[1])}») بسته شد." if rr
                              else f"↪️ تسک #{_fa(tid)} باز نبود یا پیدا نشد.")

        if r["correction"]:
            done_lines.append(f"📝 اصلاح لحاظ شد: {html.escape(r['correction'])}")

        ack = r["ack"] or "چشم مدیر، اعمال شد."
        body = ack if not done_lines else ack + "\n\n" + "\n".join(done_lines)
        await msg.reply_text(body, parse_mode=ParseMode.HTML)


def _is_holiday(day) -> bool:
    """آیا این روز، تعطیلِ عمومیِ کلِ تیم اعلام شده؟ (توسطِ مدیر)."""
    return db.get_meta(f"holiday:{day}") == "1"


def _set_holiday(day, on=True):
    db.set_meta(f"holiday:{day}", "1" if on else "0")


def _pending_answer_rid(user_id):
    """ridِ گزارشی که سؤالش پرسیده شده ولی هنوز پاسخ/ارزیابی نشده — بازیابیِ حالتِ «منتظرِ پاسخ» پس از ری‌استارت."""
    cutoff = time.time() - 2 * 24 * 3600
    with db._lock:
        r = db._conn.execute(
            "SELECT id FROM wt_reports WHERE user_id=? AND kind='work' "
            "AND COALESCE(ai_questions,'')<>'' AND COALESCE(ai_answers,'')='' AND COALESCE(ai_score,'')='' "
            "AND created_ts>=? ORDER BY id DESC LIMIT 1", (int(user_id), cutoff)).fetchone()
    return r[0] if r else None


# ---------- هوکِ پیامِ گروه (از on_text صدا زده می‌شود) ----------
async def on_group_message(update, context) -> bool:
    """wrapperِ idempotency ورودی (D-01): تحویلِ دوبارهٔ همین update دوباره پردازش/ثبت نمی‌شود.

    منطقِ شاخه‌ها در _dispatch_group_message است. اگر پیام مربوط به این ماژول بود True برمی‌گرداند.
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return False
    wg = _workgroup()
    if not wg or chat.id != wg:
        return False
    _seen(user)  # کشفِ خودکارِ پرسنل
    ev_id = getattr(update, "update_id", 0)
    claim, _row = taskservice.claim_inbound("telegram", ev_id, operation="group_message", actor_id=user.id)
    if claim in ("duplicate", "in_progress", "skip_permanent"):
        return True  # تحویلِ دوباره → دوباره ثبت/پردازش نکن (گزارش/تسک دوباره ساخته نمی‌شود)
    try:
        handled = await _dispatch_group_message(update, msg, user, ev_id)
        taskservice.complete_inbound("telegram", ev_id, result_code=("handled" if handled else "ignored"))
        return handled
    except Exception as e:  # noqa: BLE001 — رویداد را برای recovery علامت بزن (mutationها idempotentاند)
        taskservice.fail_inbound("telegram", ev_id, type(e).__name__)
        raise


async def _dispatch_group_message(update, msg, user, ev_id) -> bool:
    """منطقِ شاخه‌ها: پاسخِ ارزیابی / گزارش / تعطیل / ریپلای مدیر / منشنِ ساختِ تسک. idempotency ورودی در wrapper است."""
    text = (msg.text or "").strip()

    # دلیلِ مسدودشدنِ تسک (پس از دکمهٔ «مسدود») — قبل از بقیهٔ شاخه‌ها
    bl = _awaiting_block.get(user.id)
    if bl and time.time() - bl[1] <= _AWAIT_TTL:
        _awaiting_block.pop(user.id, None)
        r = lifecycle_block(bl[0], user.id, text)
        await msg.reply_text("⛔ ثبت شد؛ به مدیر اطلاع می‌رسد و کمکت می‌کنیم." if r.status == "applied"
                             else "الان قابلِ ثبت نیست.")
        return True

    # پاسخِ سؤالاتِ ارزیابیِ AI (بعد از گزارش) — با بازیابیِ DB اگر حالتِ حافظه با ری‌استارت پاک شده باشد
    rid = _awaiting_answers.pop(user.id, None)
    if not rid and not (not _is_admin(user.id) and _parse_attendance(text)):
        rid = _pending_answer_rid(user.id)  # ولی گزارشِ تازه (فرمتِ حضوروغیاب) را پاسخ نگیر
    if rid:
        await _finalize_eval(msg, user, rid, text)
        return True

    # اگر با دکمه‌ی «ثبتِ گزارش» منتظرِ متن بودیم، همین پیام گزارشِ روزانه است
    if user.id in _awaiting:
        if time.time() - _awaiting.pop(user.id, 0) <= _AWAIT_TTL:
            await _process_report(msg, user, text)
            return True

    # مدیر «تعطیل» اعلام می‌کند → تعطیلِ عمومیِ کلِ تیم برای امروز
    if _is_admin(user.id) and _leave_kind(text) == "holiday":
        _set_holiday(clock.tehran_now().strftime("%Y-%m-%d"))
        await msg.reply_text(
            "📴 امروز برای <b>کلِ تیم</b> «تعطیل» ثبت شد؛ گزارش، یادآوری و ارزیابیِ امروز غیرفعال شد. "
            "روزِ خوبی داشته باشید 🌿",
            parse_mode=ParseMode.HTML)
        return True

    # ریپلای مدیر روی پیامِ ربات = فرمان/اصلاح/دستورِ دائمی (حلقه‌ی بازخوردِ مدیر)
    rep = msg.reply_to_message
    if _is_admin(user.id) and rep and rep.from_user and rep.from_user.is_bot and wt_brain.enabled():
        await _handle_manager_reply(msg, user, ev_id=ev_id)
        return True

    # گزارشِ روزانه: پیامی که با «گزارش» شروع شود
    if text.startswith("گزارش"):
        body = text[len("گزارش"):].lstrip(" :،-").strip() or text
        await _process_report(msg, user, body)
        return True

    # گزارشِ بدونِ دکمه/پیشوند: پیامِ کارمند با فرمتِ حضوروغیاب → گزارشِ روزانه
    if not _is_admin(user.id) and _parse_attendance(text):
        await _process_report(msg, user, text)
        return True

    # ثبتِ تسک: فقط مدیر، با منشن (idempotency ورودی در wrapper؛ فقط کلیدِ سطحِ عملیات اینجا)
    if not _is_admin(user.id):
        return False
    assignees = _mentioned_users(msg)
    if not assignees:
        return False
    for idx, (aid, aname) in enumerate(assignees):
        _seen_id(aid, aname)
        ctx = taskservice.MutationContext(
            actor_id=user.id, actor_role=_role_of(user.id), source="telegram", operation="task_create",
            source_event_id=str(ev_id), idempotency_key=f"telegram:{ev_id}:task_create:{idx}:{aid}")
        _add_task(aid, aname, user.id, user.full_name, text, ctx=ctx)
    who = "، ".join(a[1] for a in assignees)
    await msg.reply_text(f"🗂️ چشم! یه تسک برای {who} ثبت شد و به لیستش اضافه شد 💪 (با /tasks می‌بینه و می‌بنده)")
    return True


# ---------- هوکِ callback (از on_callback صدا زده می‌شود) ----------
async def on_callback_hook(q, context) -> bool:
    """callbackهای wt:… را هندل می‌کند (done / tasks / report / team). اگر مربوط بود True."""
    data = q.data or ""
    if not data.startswith("wt:"):
        return False
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    uid = q.from_user.id if q.from_user else 0
    if q.from_user:
        _seen(q.from_user)

    if action in ("done", "start", "block", "resume"):
        try:
            tid = int(parts[2])
        except (ValueError, IndexError):
            await _ans(q)
            return True
        # مسیرِ چرخه (flag روشن): start/block/resume/done از سرویسِ گذار. خاموش = رفتارِ legacyِ done.
        if _lc_enabled():
            if action == "start":
                r = lifecycle_start(tid, uid, idem=f"telegram:cb:{q.id}:start:{tid}")
                await _ans(q, "▶️ شروع شد، موفق باشی!" if r.status == "applied" else "الان قابلِ شروع نیست.")
            elif action == "resume":
                r = lifecycle_resume(tid, uid, idem=f"telegram:cb:{q.id}:resume:{tid}")
                await _ans(q, "▶️ ادامه بده 💪" if r.status == "applied" else "الان قابلِ ادامه نیست.")
            elif action == "block":
                _awaiting_block[uid] = (tid, time.time())
                await _ans(q, "دلیلِ مسدودشدن را همین‌جا بنویس ✍️")
                try:
                    await q.message.reply_text("⛔ چه چیزی مانع شده؟ کوتاه بنویس تا مدیر ببیند و کمک کند.")
                except Exception:
                    pass
                return True
            else:  # done
                r, target = lifecycle_done(tid, uid, idem=f"telegram:cb:{q.id}:done:{tid}")
                if r.status == "applied":
                    msg_ok = ("🎉 عالی! ثبت و تأیید شد 💪" if target == "verified_done"
                              else "📨 ثبت شد؛ برای تأییدِ مدیر رفت 🙌")
                    await _ans(q, msg_ok)
                    if target == "claimed_done":  # صحت‌سنجیِ خودکار (اگر mode=automatic) خارج از تراکنش
                        asyncio.create_task(verify_and_apply(tid))
                elif r.status == "noop":
                    await _ans(q, "قبلاً ثبت شده.")
                else:
                    await _ans(q, "این تسک مالِ تو نیست یا در این وضعیت نیست.", alert=True)
            await _refresh_task_list(q, uid)
            return True
        # ---- مسیرِ legacy (flag خاموش): دقیقاً مثلِ قبل ----
        if action != "done":
            await _ans(q)
            return True
        ctx = taskservice.MutationContext(actor_id=uid, actor_role=_role_of(uid), source="telegram",
                                          operation="task_mark_done", source_event_id=str(q.id),
                                          idempotency_key=f"telegram:cb:{q.id}:task_mark_done:{tid}")
        if _task_done(tid, uid, ctx=ctx):
            await _ans(q, "🎉 آفرین! انجام شد 💪")
            try:
                rows = _open_tasks(uid)
                if rows:
                    await q.edit_message_text(_tasks_text(rows), parse_mode=ParseMode.HTML, reply_markup=_tasks_kb(rows))
                else:
                    await q.edit_message_text("✅ همه‌ی تسک‌هایت بسته شد. آفرین! 🎉")
            except Exception:
                pass
        else:
            await _ans(q, "این تسک مالِ تو نیست یا قبلاً بسته شده.", alert=True)
        return True

    if action == "linkwp":  # انتخابِ کاربرِ وردپرس برای لینک به پرسنل
        if not _is_admin(uid):
            await _ans(q, "فقط برای مدیران است.", alert=True)
            return True
        try:
            tg_uid, wp_id = parts[2], parts[3]
        except IndexError:
            await _ans(q)
            return True
        db.set_meta(f"wp_link:{tg_uid}", wp_id)
        name = _staff_name(tg_uid) or tg_uid
        await _ans(q, "ثبت شد ✅")
        try:
            await q.edit_message_text(
                f"✅ «{html.escape(str(name))}» به کاربرِ وردپرسِ {_fa(int(wp_id))} لینک شد؛ "
                f"کارِ واقعیِ ثبت‌شده‌اش در سایت در ارزیابیِ عملکرد چک می‌شود.")
        except Exception:  # noqa: BLE001
            pass
        return True

    if action == "noop":  # دکمهٔ نمایشیِ «منتظرِ تأیید» — فقط ack
        await _ans(q)
        return True

    if action in ("salok", "salno"):  # تأیید/لغوِ ثبتِ حقوق (فقط مدیرِ اصلی، صاحبِ pending)
        if not _is_primary_admin(uid):
            await _ans(q, "فقط مدیرِ اصلی.", alert=True)
            return True
        pend = _pending_salary.pop(uid, None)
        if action == "salno" or not pend:
            await _ans(q, "لغو شد" if action == "salno" else "موردی برای تأیید نبود.")
            try:
                await q.edit_message_text("✖️ ثبتِ حقوق لغو شد.")
            except Exception:  # noqa: BLE001
                pass
            return True
        ok = wt_hr.set_salary(uid, pend["pid"], pend["amount"], pend["method"])
        await _ans(q, "ثبت شد ✅" if ok else "ثبت نشد.")
        try:
            if ok:
                await q.edit_message_text(f"✅ حقوقِ {html.escape(pend['name'])} ثبت شد: "
                                          f"{wt_hr.method_fa(pend['method'])} — {wt_hr.fmt_money(pend['amount'])}")
        except Exception:  # noqa: BLE001
            pass
        return True

    if action == "tasks":  # «تسک‌های من»
        await _ans(q)
        rows = _open_tasks(uid)
        if rows:
            await _reply_tasks(q.message, rows, user_id=uid)
        else:
            try:
                await q.message.reply_text("✅ آفرین! هیچ تسکِ بازی نداری، همه‌چیز به‌روزه 🎉")
            except Exception:  # noqa: BLE001
                pass
        return True

    if action == "report":  # «ثبتِ گزارش» → منتظرِ متنِ بعدی می‌شویم
        _awaiting[uid] = time.time()
        await _ans(q, "بنویس و بفرست ✍️")
        try:
            name = q.from_user.full_name if q.from_user else ""
            await q.message.reply_text(f"📝 {html.escape(name)} جان، گزارشِ امروزت را همین‌جا بنویس و بفرست:")
        except Exception:
            pass
        return True

    if action == "team":  # «وضعیتِ تیم» (فقط مدیر)
        if not _is_admin(uid):
            await _ans(q, "فقط مدیران.", alert=True)
            return True
        await _ans(q)
        try:
            await q.message.reply_text(_team_status_text(), parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return True

    await _ans(q)
    return True


async def _ans(q, text="", alert=False):
    try:
        await q.answer(text, show_alert=alert)
    except Exception:
        pass


# ---------- دستورها ----------
def _tasks_text(rows) -> str:
    lines = [f"🗂️ <b>تسک‌های بازِ تو</b> ({_fa(len(rows))}) — مطمئنم از پسشون برمیای 💪", ""]
    for tid, text, assigner in rows:
        lines.append(f"• <code>#{tid}</code> — {text}  <i>(از {assigner or '—'})</i>")
    lines += ["", "<i>هر کدوم رو تموم کردی، دکمهٔ زیرش رو بزن ✅ — قدم‌به‌قدم عالی پیش می‌ری.</i>"]
    return "\n".join(lines)


def _tasks_kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ انجام شد #{tid}", callback_data=f"wt:done:{tid}")]
                                 for tid, _t, _a in rows])


# ---------- نمایشِ تسک با چرخه (وقتی flag روشن است) ----------
_LC_LABEL = {"open": "شروع‌نشده", "in_progress": "در حالِ انجام", "blocked": "مسدود",
             "claimed_done": "منتظرِ تأیید", "reopened": "بازگشایی‌شده"}


def _lc_open_tasks(user_id):
    """تسک‌های غیرِ terminalِ کاربر با stateِ چرخه: [(id, text, assigner, state)]."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, text, assigner_name, lifecycle_state, status FROM wt_tasks "
            "WHERE assignee_id=? AND status='open' ORDER BY id", (user_id,)).fetchall()
    return [(tid, text, asg, taskservice.lifecycle_of(lc, st)) for tid, text, asg, lc, st in rows]


def _lc_tasks_text(rows) -> str:
    lines = [f"🗂️ <b>کارهای تو</b> ({_fa(len(rows))}) — قدم‌به‌قدم عالی پیش می‌ری 💪", ""]
    for tid, text, asg, state in rows:
        lines.append(f"• <code>#{tid}</code> [{_LC_LABEL.get(state, state)}] {text}  <i>(از {asg or '—'})</i>")
    return "\n".join(lines)


def _lc_tasks_kb(rows):
    kb = []
    for tid, _t, _a, state in rows:
        row = []
        if state in ("open", "reopened"):
            row.append(InlineKeyboardButton(f"▶️ شروع #{tid}", callback_data=f"wt:start:{tid}"))
            row.append(InlineKeyboardButton("✅ انجام شد", callback_data=f"wt:done:{tid}"))
        elif state == "in_progress":
            row.append(InlineKeyboardButton(f"⛔ مسدود #{tid}", callback_data=f"wt:block:{tid}"))
            row.append(InlineKeyboardButton("✅ انجام شد", callback_data=f"wt:done:{tid}"))
        elif state == "blocked":
            row.append(InlineKeyboardButton(f"▶️ ادامه #{tid}", callback_data=f"wt:resume:{tid}"))
            row.append(InlineKeyboardButton("✅ انجام شد", callback_data=f"wt:done:{tid}"))
        elif state == "claimed_done":
            row.append(InlineKeyboardButton(f"⏳ منتظرِ تأیید #{tid}", callback_data="wt:noop"))
        if row:
            kb.append(row)
    return InlineKeyboardMarkup(kb) if kb else None


async def _refresh_task_list(q, uid):
    """پس از هر اکشنِ چرخه، لیستِ تسکِ کاربر را به‌روز نمایش می‌دهد (fail-soft)."""
    try:
        rows = _lc_open_tasks(uid)
        if rows:
            await q.edit_message_text(_lc_tasks_text(rows), parse_mode=ParseMode.HTML, reply_markup=_lc_tasks_kb(rows))
        else:
            await q.edit_message_text("✅ همه‌ی کارهایت به‌روزه. آفرین! 🎉")
    except Exception:  # noqa: BLE001
        pass


async def cmd_setworkgroup(update, context):
    """این گروه را به‌عنوانِ «گروهِ گزارشِ کار» ثبت می‌کند (فقط مدیر، داخلِ گروه)."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat:
        return
    if chat.type == "private":
        await msg.reply_text("این دستور را داخلِ گروهِ گزارشِ کار بفرست.")
        return
    if not user or not _is_admin(user.id):
        await msg.reply_text("فقط مدیران می‌توانند گروه را ثبت کنند.")
        return
    db.set_meta("work_group", str(chat.id))
    await msg.reply_text(
        "✅ این گروه به‌عنوانِ «گروهِ گزارشِ کار» ثبت شد.\n\n"
        "• مدیر با <b>منشنِ</b> کاربر تسک می‌دهد (به تسک‌های او افزوده می‌شود).\n"
        "• هر کس با /work یا دکمه‌های زیر کارهایش را می‌بیند.\n"
        "• گزارشِ روزانه: دکمه‌ی «📝 ثبتِ گزارش» یا پیامی که با «گزارش» شروع شود.\n"
        "• پایانِ شیفت، هرکس گزارش نداده باشد یادآوری می‌شود.",
        parse_mode=ParseMode.HTML, reply_markup=work_menu_kb(True),
    )


async def cmd_tasks(update, context):
    """تسک‌های بازِ کاربر + دکمه‌ی «انجام شد»."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    _seen(user)
    rows = _open_tasks(user.id)
    if not rows:
        await msg.reply_text("✅ آفرین! هیچ تسکِ بازی نداری، همه‌چیز به‌روزه 🎉")
        return
    await _reply_tasks(msg, rows, user_id=user.id)


async def cmd_report(update, context):
    """ثبتِ گزارشِ روزانه: /report متنِ گزارش."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    _seen(user)
    body = ""
    if msg.text and " " in msg.text:
        body = msg.text.split(None, 1)[1].strip()
    if not body:
        await msg.reply_text("متنِ گزارش را بعد از دستور بنویس. مثال:\n<code>/report امروز ۵ مشتری پیگیری شد و ۲ فروش قطعی شد.</code>",
                             parse_mode=ParseMode.HTML)
        return
    await _process_report(msg, user, body)


# ---------- منوی دکمه‌ای ----------
def work_menu_kb(is_admin=False):
    rows = [
        [InlineKeyboardButton("🗂️ تسک‌های من", callback_data="wt:tasks")],
        [InlineKeyboardButton("📝 ثبتِ گزارشِ روزانه", callback_data="wt:report")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👥 وضعیتِ تیم", callback_data="wt:team")])
    return InlineKeyboardMarkup(rows)


async def cmd_work(update, context):
    """منوی دکمه‌ایِ گزارشِ کار: تسک‌های من / ثبتِ گزارش / (مدیر) وضعیتِ تیم."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    _seen(user)
    await msg.reply_text("🗂️ <b>مرکزِ گزارشِ کار</b>\nسلام قهرمان 👋 یکی رو انتخاب کن 👇",
                         parse_mode=ParseMode.HTML, reply_markup=work_menu_kb(_is_admin(user.id)))


# ---------- وضعیتِ تیم + یادآوری ----------
def _workers_and_reports(day):
    """پرسنلِ غیرِ مدیر (که تسک گرفته یا گزارش داده) + مجموعه‌ی گزارش‌دهندگانِ امروز + تعدادِ تسکِ باز."""
    with db._lock:
        workers = db._conn.execute(
            """SELECT DISTINCT user_id, name FROM wt_staff
               WHERE user_id IN (SELECT assignee_id FROM wt_tasks)
                  OR user_id IN (SELECT user_id FROM wt_reports)"""
        ).fetchall()
        reported = {r[0] for r in db._conn.execute("SELECT user_id FROM wt_reports WHERE day=?", (day,)).fetchall()}
        opencnt = dict(db._conn.execute(
            "SELECT assignee_id, COUNT(*) FROM wt_tasks WHERE status='open' GROUP BY assignee_id").fetchall())
    admins = set(config.ADMIN_USER_IDS)
    # مدیران و پرسنلِ قطع‌همکاری‌شده از فهرستِ «کارمندان» کنار می‌روند (نه یادآوری، نه در کارتِ عملکرد).
    workers = [(u, n) for u, n in workers if u not in admins and not _is_retired(u)]
    return workers, reported, opencnt


def workers_without_report(day):
    """پرسنلِ غیرِ مدیر که امروز گزارش نداده‌اند: [(user_id, name)]."""
    workers, reported, _ = _workers_and_reports(day)
    return [(u, n) for u, n in workers if u not in reported]


def _team_status_text() -> str:
    today = clock.tehran_now().strftime("%Y-%m-%d")
    workers, reported, opencnt = _workers_and_reports(today)
    lines = [f"👥 <b>وضعیتِ تیم — امروز</b>", ""]
    if not workers:
        lines.append("هنوز پرسنلی ثبت نشده (با منشن در گروه تسک بده تا شناسایی شوند).")
        return "\n".join(lines)
    for uid, name in workers:
        rep = "گزارش ✅" if uid in reported else "گزارش ❌"
        lines.append(f"• {html.escape(name)} — {rep} · تسکِ باز: {_fa(opencnt.get(uid, 0))}")
    done = sum(1 for u, _ in workers if u in reported)
    lines += ["", f"گزارش‌دهنده: {_fa(done)}/{_fa(len(workers))}"]
    return "\n".join(lines)


# دو نوبتِ یادآوریِ گزارش (به‌وقتِ تهران): ۲۱:۰۰ و ۲۳:۳۰ — هر کدام گاردِ روزانه‌ی خودش.
_REPORT_NUDGE_SLOTS = [(21 * 60, "last_nudge_2100"), (23 * 60 + 30, "last_nudge_2330")]


# ---------- ارسالِ امنِ مشترک (رفعِ D-RG-01) ----------
# روشِ کوچکِ مشترک برای همهٔ ارسال‌های زمان‌بندی‌شده (یادآوری/کارتِ عملکرد/نوتیفیکیشنِ چرخه).
# الگوی صحیح: claim (مهلت‌دار) → send → complete. علامتِ موفقیت هرگز قبل از ارسالِ واقعی ست نمی‌شود.
# روی in_progress هیچ گاردِ بیرونی ست نمی‌شود تا با انقضای lease، recovery و تلاشِ دوباره ممکن بماند
# (پیش‌تر ست‌شدنِ گاردِ meta روی in_progress باعثِ گم‌شدنِ دائمیِ پیام می‌شد — همان D-RG-01).
async def _guarded_send(dkey, operation, send_coro):
    """خروجی status ∈ {sent, duplicate, in_progress, skip_permanent, failed}. تحویل = at-least-once.

    - claimed/recovered → ارسال، سپس complete (status=sent).
    - duplicate/skip_permanent → قبلاً نهایی/تمام‌شده (کالر می‌تواند گاردِ خودش را هم‌راستا کند).
    - in_progress → کسِ دیگری در جریان است؛ **گارد ست نکن** تا recovery ممکن بماند (رفعِ گم‌شدنِ دائمی).
    - failed → این تلاش شکست خورد؛ retryable در دورِ بعد (گارد ست نمی‌شود).
    پنجرهٔ مبهمِ «رسیدنِ پیام تا ثبتِ complete» ممکن است duplicate بسازد؛ این محدودیت پذیرفته و صریح ثبت شده است.
    """
    dclaim, _ = taskservice.delivery_claim(dkey, operation=operation)
    if dclaim in ("duplicate", "skip_permanent"):
        return dclaim
    if dclaim == "in_progress":
        return "in_progress"
    try:  # claimed | recovered
        m = await send_coro()
        taskservice.delivery_complete(dkey, message_id=getattr(m, "message_id", ""))
        return "sent"
    except Exception as e:  # noqa: BLE001
        taskservice.delivery_fail(dkey, type(e).__name__)
        return "failed"


async def maybe_report_reminder(app):
    """دو نوبت (۲۱:۰۰ و ۲۳:۳۰): به پرسنلی که هنوز گزارشِ امروز را نداده‌اند در گروهِ کار با منشن یادآوری کن.

    «امروز» = روزِ کاری‌ای که هنوز جمع نشده؛ چون گزارشِ هر روز معمولاً همان شب/شبِ بعد می‌آید.
    """
    import poller  # واردسازیِ تنبل (پرهیز از حلقه)
    now = clock.tehran_now()
    if poller._shift_end_min(now) is None:  # جمعه → بی‌خبر
        return
    today = now.strftime("%Y-%m-%d")
    if _is_holiday(today):  # تعطیلِ عمومیِ اعلام‌شده → یادآوری نکن
        return
    nowmin = now.hour * 60 + now.minute
    due = [key for mn, key in _REPORT_NUDGE_SLOTS if nowmin >= mn and db.get_meta(key) != today]
    if not due:  # هنوز به هیچ نوبتی نرسیده‌ایم یا همه مصرف شده‌اند
        return
    group = _workgroup()
    if not group:
        return
    missing = workers_without_report(today)
    if not missing:  # همه دادند → نوبت‌های سررسیدشده را بی‌ارسال «مصرف‌شده» علامت بزن
        for key in due:
            db.set_meta(key, today)
        return
    mentions = " ".join(f'<a href="tg://user?id={uid}">{html.escape(name)}</a>' for uid, name in missing)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📝 ثبتِ گزارش", callback_data="wt:report")]])
    # رفعِ D-RG-01: از الگوی امنِ مشترک. گاردِ metaِ روزانه فقط پس از ارسالِ قطعی/duplicate ست می‌شود،
    # نه روی in_progress/failed — تا crashِ قبل از ارسال به گم‌شدنِ دائمی منجر نشود (recovery باقی می‌ماند).
    dkey = f"reminder:{today}:" + "+".join(sorted(due))
    txt = ("🌸 <b>یادآوریِ گزارش</b>\nمی‌دونم حسابی زحمت کشیدید 💚 "
           "این عزیزان فقط گزارششون مونده — هر وقت فرصت کردی چند خط با تاریخ و ساعتِ ورود–خروج برامون بنویس "
           "تا زحماتت کامل دیده بشه 👇\n" + mentions)
    status = await _guarded_send(dkey, "report_reminder",
                                 lambda: app.bot.send_message(group, txt, parse_mode=ParseMode.HTML, reply_markup=kb))
    if status in ("sent", "duplicate", "skip_permanent"):  # نهایی‌شده (فرستاده/قبلاً‌فرستاده/سقفِ تلاش) → گارد هم‌راستا
        for key in due:
            db.set_meta(key, today)
        if status == "sent":
            print(f"[worktasks] یادآوریِ گزارش به {len(missing)} نفر ارسال شد ({'،'.join(due)}).")
        elif status == "skip_permanent":
            print(f"[worktasks] یادآوریِ گزارش پس از سقفِ تلاش نهایی‌شد (failed_permanent) — {'،'.join(due)}.")
    # in_progress/failed → گارد ست نمی‌شود؛ دورِ بعد پس از انقضای lease دوباره تلاش می‌شود.


# ---------- گزارشِ مدیران (روزانه + ماهانه، فقط مدیران) ----------
def _bullets(s) -> str:
    """رشته‌ی «a | b | c» → خطوطِ بولت‌دار (برای باقی‌مانده/موانع)."""
    return "".join(f"\n     ▪️ {html.escape(x.strip())}" for x in str(s or "").split("|") if x.strip())


def daily_perf_text(day) -> str:
    """کارتِ عملکردِ روزانه‌ی تیم: نمره + خلاصه + کارهای باقی‌مانده + موانع + تسکِ باز (تاریخِ شمسی)."""
    workers, _reported, opencnt = _workers_and_reports(day)
    with db._lock:
        rows = db._conn.execute(
            "SELECT user_id, user_name, ai_score, ai_summary, ai_flags, ai_remaining, ai_blockers, kind, "
            "ai_carryover, ai_tasks, ai_growth "
            "FROM wt_reports WHERE day=? ORDER BY id", (day,)).fetchall()
    latest = {r[0]: r for r in rows}  # آخرین گزارشِ هر کاربر در آن روز
    lines = [f"📊 <b>کارتِ عملکردِ تیم — {_jalali(day)}</b>", ""]
    holiday = _is_holiday(day)
    if holiday:
        lines += ["📴 <b>امروز تعطیلِ عمومی اعلام شد</b> — گزارش و ارزیابی لازم نبود.", ""]
    if not workers:
        lines.append("هنوز پرسنلی ثبت نشده.")
        return "\n".join(lines)
    scores = []
    for uid, name in workers:
        op = _fa(opencnt.get(uid, 0))
        r = latest.get(uid)
        if r:
            _u, _n, score, summ, flags, remaining, blockers, kind, carryover, aitasks, growth = r
            if kind in ("leave", "holiday"):
                label = "🌴 مرخصی" if kind == "leave" else "📴 تعطیل"
                lines.append(f"👤 <b>{html.escape(name)}</b>  ·  {label}")
                lines.append("")
                continue
            sc = f"{_fa(score)}/۱۰۰" if score is not None else "—"
            lines.append(f"👤 <b>{html.escape(name)}</b>  ·  نمره {sc}  ·  تسکِ باز {op}")
            if summ:
                lines.append(f"   └ {html.escape(summ)}")
            if carryover:
                co_lines = "".join(f"\n        {html.escape(x.strip())}" for x in str(carryover).split("|") if x.strip())
                lines.append(f"   🔁 <b>راستی‌آزماییِ مانده:</b>{co_lines}")
            if remaining:
                lines.append(f"   🔸 <b>باقی‌مانده:</b>{_bullets(remaining)}")
            if blockers:
                lines.append(f"   ⛔ <b>موانع:</b>{_bullets(blockers)}")
            if aitasks:
                lines.append(f"   🎯 <b>تسک‌های فردا:</b> {html.escape(str(aitasks).replace(' | ', '  ·  '))}")
            if growth:
                lines.append(f"   🌱 <b>رشد:</b> {html.escape(str(growth).replace(' | ', '؛ '))}")
            if flags:
                lines.append(f"   ⚠️ {html.escape(str(flags).replace('|', '،'))}")
            if score is not None:
                scores.append(score)
        else:
            if holiday:  # روزِ تعطیل: کسی که گزارش نداده مؤاخذه نشود
                continue
            lines.append(f"👤 <b>{html.escape(name)}</b>  ·  ❌ گزارش نداد  ·  تسکِ باز {op}")
        lines.append("")
    tail = f"🗣️ گزارش‌دهنده: {_fa(len(latest))}/{_fa(len(workers))}"
    if scores:
        tail = f"⭐ میانگینِ نمره: {_fa(round(sum(scores) / len(scores)))}/۱۰۰  ·  " + tail
    lines.append(tail)
    return "\n".join(lines)


def monthly_trend_text(month) -> str:
    """روندِ ماهانه‌ی هر نفر: میانگینِ نمره + تعدادِ روزهای گزارش‌داده."""
    admins = set(config.ADMIN_USER_IDS)
    with db._lock:
        rows = db._conn.execute(
            """SELECT user_id, user_name, COUNT(DISTINCT day), AVG(ai_score)
               FROM wt_reports WHERE day LIKE ? GROUP BY user_id ORDER BY AVG(ai_score) DESC""",
            (month + "%",)).fetchall()
    lines = [f"📈 <b>روندِ ماهانه — {_jalali_month(month)}</b>", ""]
    any_row = False
    for uid, name, days, avg in rows:
        if uid in admins:
            continue
        any_row = True
        a = f"{_fa(round(avg))}/۱۰۰" if avg is not None else "—"
        lines.append(f"• <b>{html.escape(name or '—')}</b> — میانگینِ نمره {a} · {_fa(days)} روز گزارش")
    if not any_row:
        lines.append("داده‌ای برای این ماه ثبت نشده.")
    return "\n".join(lines)


_PERF_FALLBACK_MIN = 23 * 60 + 50  # ۲۳:۵۰ — پس از آخرین یادآوری (۲۳:۳۰)؛ اگر هنوز همه ندادند، کارت با علامتِ نداده‌ها می‌رود


async def maybe_manager_report(app):
    """گزارشِ عملکردِ روزانه به پیویِ مدیران — فقط وقتی «همه گزارش دادند» (تا آن‌موقع صبر می‌کند).

    فالبک: اگر تا ۲۳:۵۰ (پس از هر دو یادآوریِ ۲۱:۰۰ و ۲۳:۳۰) هنوز همه گزارش نداده بودند، کارت
    (با علامتِ ❌ نداده‌ها) فرستاده می‌شود تا مدیر بی‌خبر نماند. مسیرِ رویدادیِ
    maybe_send_perf_when_complete هم به‌محضِ گزارشِ آخرین نفر، کارتِ کامل را می‌فرستد.
    """
    import poller
    import telegram_io
    now = clock.tehran_now()
    end = poller._shift_end_min(now)
    if end is None:  # جمعه
        return
    nowmin = now.hour * 60 + now.minute
    if nowmin < end:  # هنوز شیفت تمام نشده
        return
    day = _latest_report_day() or now.strftime("%Y-%m-%d")  # روزِ خودِ گزارش، نه today
    if db.get_meta("last_perf_report") == day or _is_holiday(day):
        return
    workers, _r, _o = _workers_and_reports(day)
    if not workers:
        return
    missing = workers_without_report(day)
    if missing and nowmin < _PERF_FALLBACK_MIN:  # تا همه گزارش ندهند صبر کن (یادآوریِ ۲۱:۰۰ و ۲۳:۳۰ نهیب می‌زند)
        return
    # رفعِ D-RG-01: الگوی امنِ مشترک؛ last_perf_report فقط پس از ارسالِ قطعی/duplicate ست می‌شود، نه روی in_progress.
    status = await _guarded_send(f"perf:{day}", "manager_perf",
                                 lambda: telegram_io.send_to_managers(app, daily_perf_text(day), parse_mode="HTML"))
    if status in ("sent", "duplicate", "skip_permanent"):
        db.set_meta("last_perf_report", day)
        if status == "sent":
            tag = "کامل (همه گزارش دادند)" if not missing else f"با {len(missing)} نفرِ گزارش‌نداده (مهلت گذشت)"
            print(f"[worktasks] گزارشِ عملکردِ روزانه به مدیران ارسال شد — {tag}.")
    # in_progress/failed → گارد ست نمی‌شود؛ دورِ بعد پس از انقضای lease دوباره تلاش می‌شود.


async def _send_managers(bot, text):
    """ارسالِ مدیرـمحور با یک bot (نه app): REPORTS_CHAT_ID یا پیویِ تک‌تکِ ادمین‌ها."""
    targets = [config.REPORTS_CHAT_ID] if config.REPORTS_CHAT_ID else list(config.ADMIN_USER_IDS)
    for t in targets:
        try:
            await bot.send_message(t, text, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            print(f"[worktasks] ارسال به مدیر {t} ناموفق: {e!r}")


def _latest_report_day():
    """روزِ کاری‌ای که آخرین گزارش برایش ثبت شده (بر پایهٔ work_date/day)، در ۲ روزِ اخیر.

    چون گزارشِ هر روز معمولاً صبحِ روزِ بعد می‌آید، «امروزِ تقویمی» با روزِ گزارش یکی نیست؛
    پس تکمیل‌بودن باید روی روزِ خودِ گزارش سنجیده شود، نه today.
    """
    cutoff = time.time() - 2 * 24 * 3600
    with db._lock:
        r = db._conn.execute(
            "SELECT day FROM wt_reports WHERE created_ts>=? ORDER BY day DESC, id DESC LIMIT 1", (cutoff,)).fetchone()
    return r[0] if r else None


async def maybe_send_perf_when_complete(bot):
    """وقتی «آخرین گزارش‌دهنده» ثبت شد (همهٔ پرسنل برای همان روزِ کاری گزارش دادند)، کارتِ عملکرد را به مدیران بفرست.

    یک‌بار در روز (گاردِ last_perf_report). اگر کسی هنوز در حالِ پاسخ به سؤالاتِ ارزیابی است، صبر می‌کند.
    روزِ سنجش = روزِ خودِ گزارش (work_date)، نه today.
    """
    day = _latest_report_day()
    if not day or _is_holiday(day) or db.get_meta("last_perf_report") == day:
        return
    if _awaiting_answers:  # کسی هنوز در حالِ ارزیابی است → هنوز آخرین نفر تمام نشده
        return
    workers, _r, _o = _workers_and_reports(day)
    if not workers or workers_without_report(day):  # هنوز همه برای این روز گزارش نداده‌اند
        return
    # رفعِ D-RG-01: الگوی امنِ مشترک (کلیدِ مشترکِ perf:day با maybe_manager_report). گارد فقط پس از ارسالِ قطعی/duplicate.
    status = await _guarded_send(f"perf:{day}", "manager_perf", lambda: _send_managers(bot, daily_perf_text(day)))
    if status in ("sent", "duplicate", "skip_permanent"):
        db.set_meta("last_perf_report", day)
        if status == "sent":
            print(f"[worktasks] همه گزارش دادند ({day}) → کارتِ عملکرد به مدیران ارسال شد.")
    # in_progress/failed → گارد ست نمی‌شود؛ دورِ بعد پس از انقضای lease دوباره تلاش می‌شود.


async def cmd_perf(update, context):
    """گزارشِ عملکردِ امروز (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not _is_admin(user.id):
        await msg.reply_text("این گزارش فقط برای مدیران است.")
        return
    day = clock.tehran_now().strftime("%Y-%m-%d")
    await msg.reply_text(daily_perf_text(day), parse_mode=ParseMode.HTML)


async def cmd_perfmonth(update, context):
    """روندِ ماهانه‌ی عملکرد (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not _is_admin(user.id):
        await msg.reply_text("این گزارش فقط برای مدیران است.")
        return
    month = clock.tehran_now().strftime("%Y-%m")
    await msg.reply_text(monthly_trend_text(month), parse_mode=ParseMode.HTML)


def _worked_hours_text(jlabel, g_from, g_to) -> str:
    """جمعِ ساعاتِ کارکردِ ماه به تفکیکِ پرسنل (بر اساسِ work_date و worked_minِ ثبت‌شده در گزارش‌ها)."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT user_name, COUNT(DISTINCT work_date), COALESCE(SUM(worked_min),0) "
            "FROM wt_reports WHERE worked_min IS NOT NULL AND work_date>=? AND work_date<=? "
            "GROUP BY user_id ORDER BY 3 DESC", (g_from, g_to)).fetchall()
    lines = [f"🕒 <b>ساعاتِ کارکرد — {jlabel}</b>", ""]
    if not rows:
        lines.append("برای این ماه هنوز گزارشِ ساعت‌داری ثبت نشده.")
        return "\n".join(lines)
    total = 0
    for name, days, mins in rows:
        mins = int(mins or 0)
        total += mins
        lines.append(f"• <b>{html.escape(name or '—')}</b> — "
                     f"{_fa(f'{mins // 60}:{mins % 60:02d}')} ساعت · {_fa(days)} روز")
    lines += ["", f"➕ جمعِ کل: <b>{_fa(f'{total // 60}:{total % 60:02d}')}</b> ساعت"]
    return "\n".join(lines)


async def cmd_hours(update, context):
    """جمعِ ساعاتِ کارکردِ ماهِ شمسی به تفکیکِ پرسنل (فقط مدیر). مثال: /hours یا /hours ۱۴۰۵/۰۴"""
    import re
    import datetime
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    arg = ((context.args or [""])[0] or "").translate(_FA_NUM).strip()
    jnow = jdatetime.date.fromgregorian(date=clock.tehran_now().date())
    jy, jmo = jnow.year, jnow.month
    m = re.search(r"(1[34]\d{2})[/\-.](\d{1,2})", arg)
    if m:
        jy, jmo = int(m.group(1)), int(m.group(2))
    try:
        start = jdatetime.date(jy, jmo, 1).togregorian()
        nxt = (jdatetime.date(jy + 1, 1, 1) if jmo == 12 else jdatetime.date(jy, jmo + 1, 1)).togregorian()
    except Exception:  # noqa: BLE001
        await msg.reply_text("ماهِ نامعتبر. مثال: /hours ۱۴۰۵/۰۴")
        return
    g_from = start.strftime("%Y-%m-%d")
    g_to = (nxt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    await msg.reply_text(_worked_hours_text(f"{_fa(jy)}/{_fa(f'{jmo:02d}')}", g_from, g_to),
                         parse_mode=ParseMode.HTML)


async def cmd_directives(update, context):
    """/directives فهرستِ دستورهای دائمیِ مدیر؛ /directives off <id> غیرفعال (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    args = context.args or []
    if len(args) >= 2 and args[0] in ("off", "حذف", "delete"):
        await msg.reply_text("✅ دستور غیرفعال شد." if _deactivate_directive(args[1]) else "چنین دستورِ فعّالی نبود.")
        return
    with db._lock:
        allrows = db._conn.execute(
            "SELECT id, scope, target_id, text, created_name, ts FROM wt_directives WHERE active=1 "
            "ORDER BY scope, ts").fetchall()
    body = _format_directives(allrows) or "دستورِ دائمیِ فعّالی ثبت نشده."
    await msg.reply_text("🧭 <b>دستورهای دائمیِ مدیر</b>\n\n" + body
                         + "\n\n<i>غیرفعال‌سازی: /directives off &lt;شماره&gt;</i>", parse_mode=ParseMode.HTML)


async def cmd_role(update, context):
    """شرحِ وظایفِ یک پرسنل را می‌نویسد/ویرایش/نمایش می‌دهد (فقط مدیر). ریپلای روی پرسنل + «/role <متن>»."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    target = _target_user(msg)
    if not target:
        await msg.reply_text("روی پیامِ پرسنل ریپلای بزن و «/role <شرحِ وظایف>» بفرست (یا منشنش کن).")
        return
    _seen_id(target[0], target[1])
    body = " ".join(context.args or []).strip()
    if not body:
        cur = _get_role(target[0])
        await msg.reply_text(
            f"📋 شرحِ وظایفِ «{html.escape(target[1])}»:\n{html.escape(cur) if cur else '— (ثبت نشده)'}\n\n"
            "ثبت/ویرایش: روی پیامش ریپلای + «/role &lt;متن&gt;».", parse_mode=ParseMode.HTML)
        return
    _set_role(target[0], body)
    await msg.reply_text(
        f"✅ شرحِ وظایفِ «{html.escape(target[1])}» ثبت شد؛ از این پس تسک‌های مرتبطِ خزش خودکار به او سپرده می‌شود.",
        parse_mode=ParseMode.HTML)


_CRAWL_TTL_DAYS = 3          # تسکِ بازِ خزشِ کهنه‌تر از این → تشدید به مدیر
_WORSEN_FACTOR = 1.5         # اگر متریک ≥۱.۵ برابر و…
_WORSEN_MIN_DELTA = 3        # …حداقل ۳ واحد بدتر شد → تسک رفرش/هشدار
_RECUR_DAYS = 2             # مشکل ظرفِ این مدت پس از done دوباره پیدا شد → «حل نشده»
_DONE_HOLD_DAYS = 3650      # تسکِ «انجام‌شده» تا این مدت دوباره ساخته نمی‌شود (خواستهٔ مالک: تکرار نشود)


async def _run_crawl(actor_id, actor_name):
    """خزش + مدیریتِ هوشمندِ تسک‌ها: dedupِ قطعی (کلید)، رفرشِ دسته‌های پویا/بدترشده، تشدیدِ کهنه‌ها،
    و نشانه‌گذاریِ «دوباره ظاهر شد». خروجی: (متنِ HTML، تعدادِ تسکِ تازه، فعالیتِ قابلِ‌اعلام)."""
    import crawler
    issues, notes = await crawler.collect()  # هر issue = {key, text, metric, dynamic}
    open_by_key = _open_crawl_by_key()
    now = time.time()
    ttl = _CRAWL_TTL_DAYS * 86400

    fresh, refreshed, escalated, skipped = [], [], [], 0
    for i in issues:
        key = i.get("key") or ""
        ex = open_by_key.get(key) if key else None
        if not ex:
            fresh.append(i)
            continue
        m_new, m_old = i.get("metric"), ex.get("metric")
        worsened = bool(m_new and m_old and m_new >= m_old * _WORSEN_FACTOR and (m_new - m_old) >= _WORSEN_MIN_DELTA)
        age = now - (ex.get("created_ts") or now)
        if i.get("dynamic") or worsened:
            _update_crawl_task(ex["id"], i["text"], m_new)
            refreshed.append((i, worsened))
        elif age > ttl:
            _bump_crawl_task(ex["id"])
            escalated.append((i, ex, age))
        else:
            skipped += 1

    assigned_lines, pending_lines, n_new = [], [], 0
    if fresh:
        staff = _staff_roles()
        det = _deterministic_route(fresh, staff) if staff else {}      # اساینِ قطعیِ بدونِ LLM
        llm_fresh = [i for i in fresh if i["key"] not in det]          # فقط مبهم‌ها → LLM
        routes = (await wt_brain.route_issues([{"key": i["key"], "text": i["text"]} for i in llm_fresh],
                                              [{"name": n, "role": d} for _u, n, d in staff])
                  if (staff and llm_fresh) else [])
        print(f"[crawl-route] deterministic={len(det)} llm_fallback={len(llm_fresh)} "
              f"llm_call={1 if (staff and llm_fresh) else 0}")
        name2uid = {n: u for u, n, d in staff}
        by_key = {i["key"]: i for i in fresh}
        done_keys = set()

        def _make(key, txt, assignee):
            nonlocal n_new
            issue = by_key.get(key)
            if not issue or key in done_keys:
                return
            done_keys.add(key)
            if _recent_done_crawl_key(key, _DONE_HOLD_DAYS * 86400):
                return   # قبلاً «انجام‌شده» علامت خورده — دوباره ساخته نشود (خواستهٔ مالک)
            if assignee and assignee in name2uid:
                tid = _add_task(name2uid[assignee], assignee, actor_id, actor_name, txt,
                                source_key=key, metric=issue.get("metric"), kind="crawl")
                if tid != -1:
                    n_new += 1
                    assigned_lines.append(f"• {html.escape(txt)} → <b>{html.escape(assignee)}</b>")
            else:
                tid = _add_task(0, "—", actor_id, actor_name, txt, source_key=key,
                                metric=issue.get("metric"), kind="crawl")
                if tid != -1:
                    n_new += 1
                    pending_lines.append(f"• {html.escape(txt)}")

        for key, assignee in det.items():  # اول اساین‌های قطعی (بدونِ LLM؛ متنِ خامِ مشکل، مثلِ مسیرِ بی‌مسئول)
            _make(key, by_key.get(key, {}).get("text") or "", assignee)
        for a in routes:
            k = a.get("key") or _match_key(a.get("task_text", ""), llm_fresh)
            _make(k, a.get("task_text") or (by_key.get(k, {}).get("text") or ""), a.get("assignee"))
        for i in fresh:  # هر مشکلی که AI برنگرداند → تسکِ بی‌مسئول (تا dedup پوششش دهد)
            _make(i["key"], i["text"], "")

    body = []
    if assigned_lines:
        body.append(f"✅ <b>{_fa(len(assigned_lines))} تسکِ تازه سپرده شد</b> (طبقِ شرحِ وظایف):")
        body += assigned_lines
    if pending_lines:
        if assigned_lines:
            body.append("")
        body.append("🕗 <b>نیازِ اساینِ دستی</b> (مسئولش مشخص نبود):")
        body += pending_lines
        body.append("<i>برای سپردن، روی همین پیام ریپلای بزن و بگو «به {نام} بده».</i>")
    if refreshed:
        body.append("")
        body.append("🔄 <b>به‌روزرسانی</b> (وضعیتِ مشکل تغییر کرد):")
        for i, w in refreshed:
            body.append(f"• {html.escape(i['text'])}" + (" ⤴️ بدتر شد" if w else ""))
    if escalated:
        body.append("")
        body.append("⏳ <b>تشدید</b> (روزهاست باز مانده و انجام نشده):")
        for i, ex, age in escalated:
            body.append(f"• {html.escape(i['text'])} — مسئول: {html.escape(str(ex.get('assignee_name') or '—'))}"
                        f" ({_fa(int(age // 86400))} روز)")
    if skipped:
        body.append("")
        body.append(f"🔁 <i>{_fa(skipped)} مشکل از قبل تسکِ باز دارد؛ دوباره ساخته نشد.</i>")

    if not issues:
        body = ["مشکلِ عملی‌ای پیدا نشد ✅"]
    elif not body:
        body = ["مشکلِ تازه‌ای نبود؛ همه از قبل تسکِ باز دارند ✅"]

    lines = ["🔎 <b>خزشِ مشکلات</b>", ""] + body
    if notes:
        lines += ["", "⚠️ " + "؛ ".join(html.escape(n) for n in notes)]
    return "\n".join(lines), n_new, n_new + len(refreshed) + len(escalated)


async def cmd_crawl(update, context):
    """خزشِ ملایمِ مشکلات و اساینِ خودکار به پرسنلِ مسئول (فقط مدیر، ضدبلاک)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    await msg.reply_text("🔎 در حال خزشِ ملایم… (چند لحظه)")
    try:
        text, _n, _a = await _run_crawl(user.id, user.full_name)
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"خزش ناموفق: {type(e).__name__}")
        return
    await msg.reply_text(text, parse_mode=ParseMode.HTML)


async def maybe_auto_crawl(app):
    """خزشِ خودکارِ اولِ شیفت (یک‌بار در روز): مشکلات را پیدا، خودکار به مسئول‌ها می‌سپارد و
    تسک‌ها را در «گروهِ کار» درج می‌کند تا تیم اولِ شیفت ببیند (فالبک: پیویِ مدیران).

    ضدبلاک: فقط یک‌بار در روز و از همان کلاینت‌های ملایمِ خزش (که به circuit-breaker احترام می‌گذارند).
    """
    if not getattr(config, "WT_AUTO_TASKS_ENABLED", False):   # فعلاً تسکِ خودکار ساخته نشود (فقط گزارشِ کار)
        return
    import poller
    now = clock.tehran_now()
    if not poller._in_shift(now) or _is_holiday(now.strftime("%Y-%m-%d")):
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_auto_crawl") == today:
        return
    db.set_meta("last_auto_crawl", today)
    try:
        text, n_new, n_activity = await _run_crawl(0, "🤖 خزشِ خودکار")
        if n_activity == 0:  # چیزِ تازه/بدترشده/تشدیدی نبود → گروه را با پیامِ خالی شلوغ نکن
            print("[worktasks] خزشِ اولِ شیفت: فعالیتِ تازه‌ای نبود؛ به گروه ارسال نشد.")
            return
        body = "🕘 <b>خزشِ اولِ شیفت — تسک‌های امروز</b>\n\n" + text
        wg = _workgroup()
        sent_group = False
        if wg:
            try:
                await app.bot.send_message(wg, body, parse_mode=ParseMode.HTML)
                sent_group = True
            except Exception as e:  # noqa: BLE001
                print(f"[worktasks] درجِ خزش در گروهِ کار ناموفق: {e!r}")
        if not sent_group:  # فالبک: گروهِ کار ثبت نشده یا ارسال نشد → به مدیران
            await _send_managers(app.bot, body)
        print(f"[worktasks] خزشِ اولِ شیفت: {_fa(n_new)} تسکِ تازه "
              f"({'گروهِ کار' if sent_group else 'مدیران'}).")
    except Exception as e:  # noqa: BLE001
        print(f"[worktasks] خزشِ خودکار ناموفق: {e!r}")


# ---------- سنجشِ خودِ مدیرِ داخلی + چک‌لیستِ راه‌اندازی ----------
def _health_text() -> str:
    import datetime
    now = clock.tehran_now()
    d7 = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    d14 = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    ts7 = (now - datetime.timedelta(days=7)).timestamp()
    with db._lock:
        ai_total = db._conn.execute(
            "SELECT COUNT(*) FROM wt_tasks WHERE assigner_name LIKE '🤖%' AND created_ts>=?", (ts7,)).fetchone()[0]
        ai_done = db._conn.execute(
            "SELECT COUNT(*) FROM wt_tasks WHERE assigner_name LIKE '🤖%' AND created_ts>=? AND status='done'",
            (ts7,)).fetchone()[0]
        directives = db._conn.execute("SELECT COUNT(*) FROM wt_directives WHERE active=1").fetchone()[0]
        s_now = db._conn.execute(
            "SELECT AVG(ai_score) FROM wt_reports WHERE day>=? AND ai_score IS NOT NULL", (d7,)).fetchone()[0]
        s_prev = db._conn.execute(
            "SELECT AVG(ai_score) FROM wt_reports WHERE day>=? AND day<? AND ai_score IS NOT NULL",
            (d14, d7)).fetchone()[0]
        recurring = db._conn.execute(
            "SELECT COUNT(*) FROM wt_reports WHERE day>=? AND ai_carryover LIKE '%🔁%'", (d7,)).fetchone()[0]
    workers, reported, _ = _workers_and_reports(now.strftime("%Y-%m-%d"))
    rate = round(100 * ai_done / ai_total) if ai_total else 0

    def _sc(v):
        return f"{_fa(round(v))}/۱۰۰" if v is not None else "—"
    arrow = ""
    if s_now is not None and s_prev is not None:
        arrow = " 📈" if s_now > s_prev + 1 else (" 📉" if s_now < s_prev - 1 else " ➖")
    return "\n".join([
        "🩺 <b>سلامتِ مدیرِ داخلی</b>", "",
        f"🤖 تسک‌های ساخته‌ی مدیرِ داخلی (۷روز): <b>{_fa(ai_total)}</b> · انجام‌شده: <b>{_fa(ai_done)}</b> ({_fa(rate)}%)",
        f"⭐ میانگینِ نمره: ۷روزِ اخیر {_sc(s_now)}{arrow} · ۷روزِ قبل {_sc(s_prev)}",
        f"🧭 دستورهای دائمیِ فعال: <b>{_fa(directives)}</b>",
        f"🔁 روزهای دارای عقب‌افتادگیِ تکرارشونده (۷روز): <b>{_fa(recurring)}</b>",
        f"👥 پرسنل: <b>{_fa(len(workers))}</b> · امروز گزارش‌داده: {_fa(sum(1 for u, _ in workers if u in reported))}",
        "", "<i>سلامتِ سرویس‌ها و سایت را سوپروایزر جداگانه هشدار می‌دهد.</i>",
    ])


async def cmd_health(update, context):
    """سنجشِ خودِ مدیرِ داخلی (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    await msg.reply_text(_health_text(), parse_mode=ParseMode.HTML)


def _setup_text() -> str:
    ig_admin = _ig_admin_uid()
    wg = _workgroup()
    with db._lock:
        staff = db._conn.execute("SELECT user_id, name FROM wt_staff ORDER BY name").fetchall()
    lines = ["🧩 <b>وضعیتِ راه‌اندازی</b>", "",
             f"گروهِ کار: {'✅ ثبت‌شده' if wg else '❌ (‏/setworkgroup‏ در گروه)'}", "",
             "<b>پرسنل</b> (برای دقتِ ارزیابی و اساینِ خودکار):"]
    if not staff:
        lines.append("— هنوز پرسنلی شناسایی نشده (با منشن در گروهِ کار تسک بده).")
    for uid, name in staff:
        role = "✅" if _get_role(uid) else "❌"
        wp = "✅" if _wp_link(uid) else "❌"
        ig = " ⭐اینستاگرام" if uid == ig_admin else ""
        lines.append(f"• {html.escape(name)}{ig} — شرحِ وظایف {role} · لینکِ وردپرس {wp}")
    lines += ["", "<i>❌ شرحِ وظایف → /role (ریپلای) | ❌ لینک → /linkwp (ریپلای) | ادمینِ اینستاگرام → /setigadmin</i>"]
    return "\n".join(lines)


async def cmd_setup(update, context):
    """چک‌لیستِ راه‌اندازی و آنبوردینگِ پرسنل (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    await msg.reply_text(_setup_text(), parse_mode=ParseMode.HTML)


def _target_user(msg):
    """کاربرِ هدف را از ریپلای یا منشن درمی‌آورد: (id, name) یا None."""
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        t = msg.reply_to_message.from_user
        return (t.id, t.full_name)
    ms = _mentioned_users(msg)
    return ms[0] if ms else None


_PWDAY = {5: "شنبه", 6: "یک‌شنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه"}


def _days_until_friday(now):
    """نامِ روزها از «امروز» تا «جمعه» (پایانِ هفتهٔ ایرانی) — برای پلنِ میان‌هفته."""
    import datetime
    out, d = [], now
    for _ in range(7):
        out.append(_PWDAY[d.weekday()])
        if d.weekday() == 4:  # جمعه
            break
        d = d + datetime.timedelta(days=1)
    return out


def _clean_model(s) -> str:
    """SKU/هشِ داخلِ [] را از نامِ محصول حذف می‌کند (فقط نامِ نمایشیِ تمیز بماند)."""
    import re
    return re.sub(r"\s*[\[\(][0-9a-zA-Z._-]{5,}[\]\)]", "", str(s or "")).strip()


def _flat(v) -> str:
    """اگر مقدار لیست بود، با فاصله به‌هم می‌چسبد — تا repr پایتونی در متن دیده نشود."""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x).strip() for x in v if str(x).strip())
    return str(v or "").strip()


def _story_lines(s) -> list:
    """استوری‌ها را به خطوطِ جداگانه و تمیز می‌شکند — چه لیست باشد، چه رشتهٔ «۱) … ۲) …» (بدونِ SKU)."""
    import re
    _num = re.compile(r"^\s*[۰-۹\d]{1,2}\s*[)\-.]\s*")  # پیشوندِ شماره‌گذاری (چون خودمان بولت می‌گذاریم)
    if isinstance(s, (list, tuple)):
        parts = [str(x) for x in s if str(x).strip()]
    else:
        # فقط ابتدای هر عدد را مرزِ شکست بگیر (lookbehind منفی تا وسطِ عددِ دورقمی نشکند)
        parts = [p.strip() for p in re.split(r"(?<![۰-۹\d])(?=[۰-۹\d]{1,2}[)\-.]\s)", str(s or "")) if p.strip()]
        if not parts and s:
            parts = [str(s)]
    return [_clean_model(_num.sub("", p)).strip() for p in parts if _clean_model(_num.sub("", p)).strip()]


def _day_task_text(d) -> str:
    """یک روزِ تقویم → متنِ تسکِ روزانهٔ تمیز و ایموجی‌دار (بدونِ SKU/تگِ HTML — امن برای هر نمایش)."""
    L = [f"📅 {_flat(d.get('day'))} · 🎬 {_flat(d.get('type'))}" + (f" · ⏰ {_flat(d.get('time'))}" if d.get("time") else "")]
    if d.get("brand"):
        L.append(f"🛍 محصول: {_clean_model(_flat(d.get('brand')))}")
    if d.get("hook"):
        L.append(f"🎣 قلاب: {_flat(d.get('hook'))}")
    if d.get("audio"):
        L.append(f"🎵 موزیک: {_flat(d.get('audio'))}")
    if d.get("hashtags"):
        L.append(f"🏷️ {_flat(d.get('hashtags'))}")
    st = _story_lines(d.get("stories"))
    if st:
        L.append("📸 استوری‌ها:")
        L += [f"   • {x}" for x in st]
    return "\n".join(str(x) for x in L).replace("<", "‹").replace(">", "›")


def _close_prev_igplan_tasks(ig_uid) -> int:
    """تسک‌های بازِ پلنِ محتواییِ قبلِ همین ادمین را (از طریقِ سرویس، با audit) می‌بندد تا انباشته نشوند."""
    # D-08: طبقه‌بندی از ستونِ task_kind='ig_plan'؛ برای رکوردهای legacy (task_kind IS NULL) fallbackِ محدود
    # و read-only به همان assigner_name LIKE — تا رکوردهای قدیمی هم درست بسته شوند بدونِ backfillِ production.
    with db._lock:
        rows = db._conn.execute(
            "SELECT id FROM wt_tasks WHERE assignee_id=? AND status='open' AND "
            "(task_kind='ig_plan' OR (task_kind IS NULL AND assigner_name LIKE '%محتوا%'))",
            (int(ig_uid),)).fetchall()
    n = 0
    for (tid,) in rows:
        if taskservice.mark_done(taskservice.system_context("task_mark_done"), tid).status == "applied":
            n += 1
    return n


def _chunk_html(text, limit=3800):
    """متنِ HTML را روی مرزِ خط به تکه‌های ≤limit تقسیم می‌کند (سقفِ ۴۰۹۶ تلگرام)."""
    chunks, cur = [], ""
    for line in (text or "").split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [""]


async def _reply_tasks(target, rows, user_id=None):
    """لیستِ تسک‌ها را (در صورتِ بلندبودن) چند‌تکه می‌فرستد؛ کیبورد روی تکهٔ آخر.

    اگر چرخه روشن باشد و user_id داده شود، نمای چرخه (شروع/مسدود/ادامه/انجام) نشان داده می‌شود؛ وگرنه نمای legacy."""
    if _lc_enabled() and user_id is not None:
        lc_rows = _lc_open_tasks(user_id)
        chunks = _chunk_html(_lc_tasks_text(lc_rows))
        kb_last = _lc_tasks_kb(lc_rows)
    else:
        chunks = _chunk_html(_tasks_text(rows))
        kb_last = _tasks_kb(rows)
    for i, ch in enumerate(chunks):
        kb = kb_last if i == len(chunks) - 1 else None
        try:
            await target.reply_text(ch, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass


def _day_card(d) -> str:
    """کارتِ تمیز و ایموجی‌دارِ یک روز برای نمایش (HTML، بدونِ SKU)."""
    head = f"📅 <b>{html.escape(_flat(d.get('day')))}</b>"
    if d.get("type"):
        head += f"  ·  🎬 {html.escape(_flat(d.get('type')))}"
    if d.get("time"):
        head += f"  ·  ⏰ {html.escape(_flat(d.get('time')))}"
    L = [head]
    mid = []
    if d.get("brand"):
        mid.append(f"🛍 <b>محصول:</b> {html.escape(_clean_model(_flat(d.get('brand'))))}")
    if d.get("hook"):
        mid.append(f"🎣 <b>قلاب:</b> {html.escape(_flat(d.get('hook')))}")
    if d.get("audio"):
        mid.append(f"🎵 <b>موزیک:</b> {html.escape(_flat(d.get('audio')))}")
    if d.get("hashtags"):
        mid.append(f"🏷️ {html.escape(_flat(d.get('hashtags')))}")
    if mid:
        L += [""] + mid
    st = _story_lines(d.get("stories"))
    if st:
        L += ["", "📸 <b>استوری‌های امروز:</b>"] + [f"   • {html.escape(x)}" for x in st]
    return "\n".join(L)


def _igplan_messages(plan, days, made) -> list:
    """خروجیِ پلن به‌صورتِ چند پیام: پیامِ خلاصه + «هر روز یک پیامِ تمیز»."""
    over = [f"📅 <b>برنامهٔ محتوایی</b> — از «{days[0] if days else 'شنبه'}» تا «جمعه»", ""]
    if plan.get("summary"):
        over += [html.escape(plan["summary"]), ""]
    if plan.get("brand_plan"):
        over += ["🏷️ <b>پوششِ برند:</b>"] + [f"• {html.escape(x)}" for x in plan["brand_plan"]]
    if made:
        over += ["", f"✅ {_fa(made)} تسکِ روزانه به ادمینِ پیج سپرده شد (با /tasks)."]
    elif not _ig_admin_uid():
        over += ["", "<i>برای ثبتِ خودکارِ تسکِ روزانه، اول با /setigadmin ادمینِ پیج را مشخص کن.</i>"]
    msgs = ["\n".join(over)]
    for d in plan.get("calendar") or []:
        msgs += _chunk_html(_day_card(d))  # هر روز یک پیام (اگر خیلی بلند بود، همان روز تکه می‌شود)
    return msgs


async def cmd_igplan(update, context):
    """تقویمِ محتواییِ اینستاگرام (مدیرِ متخصصِ ساعت) + ثبتِ تسک برای ادمینِ پیج (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    days = _days_until_friday(clock.tehran_now())
    wait = await msg.reply_text(f"📅 در حال ساختِ برنامهٔ محتواییِ کامل از «{days[0]}» تا «جمعه» "
                                "(با ≥۱۰ استوریِ روزانه)… لطفاً ~۲ تا ۳ دقیقه صبر کن.")
    plan, made = await _build_and_assign_igplan(user.id, days, feature="ig_content_plan_ondemand")
    if not plan:
        await wait.edit_text("ساختِ برنامه فعلاً ممکن نشد (آنالیز/مغزِ AI پاسخ نداد). کمی بعد دوباره بزن.")
        return
    msgs = _igplan_messages(plan, days, made)  # [۰]=خلاصه، سپس «هر روز یک پیامِ تمیز»
    try:
        await wait.edit_text(msgs[0], parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        await wait.edit_text("✅ برنامهٔ محتوایی آماده شد 👇")
        await msg.reply_text(msgs[0], parse_mode=ParseMode.HTML)
    for ch in msgs[1:]:
        try:
            await msg.reply_text(ch, parse_mode=ParseMode.HTML)
        except Exception:  # noqa: BLE001
            pass


async def _build_and_assign_igplan(actor_id, days, feature="ig_content_plan"):
    """پلنِ روزآگاه می‌سازد، مدل‌ها را ذخیره می‌کند و تسکِ روزانهٔ ریز به ادمینِ پیج می‌سپارد (با بستنِ پلنِ قبل).

    خروجی: (plan یا None، تعدادِ تسکِ روزانه). مشترکِ /igplan و خزشِ خودکارِ شنبه.
    feature: تفکیکِ هزینه بینِ on-demand (/igplan) و scheduled (خودکارِ شنبه).
    """
    r = await igstats.summary()
    if not r.get("ok"):
        r = {"ok": False}  # IG قطع است → پلن از موجودیِ سایت + استراتژی/سناریوی عمومی ساخته می‌شود (نه هیچ)
    inventory = await igstats.instock_by_brand()               # از سایت است، مستقلِ IG (تعداد≥۱، چرخشی)
    rivals_brief = igstats.rivals_brief_stored()                # از اسنپ‌شاتِ ذخیره‌شده (سریع)
    covered = " | ".join(db.last_plan_models(14))               # عدمِ تکرارِ مدل‌های اخیر
    plan = await wt_brain.ig_content_plan(r, inventory, rivals_brief, covered, days, feature=feature)
    cal = (plan or {}).get("calendar") or []
    if not cal:
        return None, 0
    models = [str(d.get("brand", "")).strip() for d in cal if str(d.get("brand", "")).strip()]
    if models:
        db.plan_add(" | ".join(models), "")
    made = 0
    ig_uid = _ig_admin_uid()
    if ig_uid:
        _close_prev_igplan_tasks(ig_uid)                       # پلنِ قبل بسته می‌شود (انباشته نشود)
        ig_name = _staff_name(ig_uid) or "ادمینِ اینستاگرام"
        for d in cal:
            _add_task(ig_uid, ig_name, actor_id, "🤖 مدیرِ محتوا", _day_task_text(d), kind="ig_plan")
            made += 1
    return plan, made


async def cmd_igweekly(update, context):
    """گزارش/فیدبکِ هفتگیِ اینستاگرام — مقایسهٔ هفته‌به‌هفته + اصلاح (مدیر یا ادمینِ اینستاگرام)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not (_is_admin(user.id) or user.id == _ig_admin_uid()):
        return
    wait = await msg.reply_text("📈 در حال آماده‌سازیِ گزارشِ هفتگی…")
    w = await igstats.weekly_review()
    await wait.edit_text(igstats.format_weekly(w), parse_mode=ParseMode.HTML)


async def maybe_ig_weekly(app):
    """شنبه‌ها یک‌بار: جمع‌بندی/فیدبکِ هفتگیِ اینستاگرام به مدیران + ادمینِ پیج (برای اصلاحِ پلنِ بعد)."""
    if not igstats.enabled():
        return
    import poller
    now = clock.tehran_now()
    if now.weekday() != 5 or not poller._in_shift(now):  # فقط شنبه، داخلِ شیفت
        return
    wk = now.strftime("%Y-%W")
    if db.get_meta("last_ig_weekly") == wk:
        return
    db.set_meta("last_ig_weekly", wk)
    try:
        w = await igstats.weekly_review()
        if not w.get("ok"):
            return
        txt = "🗓️ <b>جمع‌بندیِ هفتگیِ اینستاگرام</b>\n\n" + igstats.format_weekly(w)
        await _send_managers(app.bot, txt)
        ig_uid = _ig_admin_uid()
        if ig_uid:
            try:
                await app.bot.send_message(ig_uid, txt, parse_mode=ParseMode.HTML)
            except Exception:  # noqa: BLE001
                pass
        print("[worktasks] گزارشِ هفتگیِ اینستاگرام ارسال شد.")
    except Exception as e:  # noqa: BLE001
        print(f"[worktasks] گزارشِ هفتگی ناموفق: {e!r}")


async def maybe_ig_autoplan(app):
    """شنبه‌ها یک‌بار: پلنِ کاملِ هفته را خودکار می‌سازد، تسکِ روزانه به ادمینِ پیج می‌سپارد و خلاصه به مدیران."""
    if not getattr(config, "WT_AUTO_TASKS_ENABLED", False):   # فعلاً تسکِ خودکار ساخته نشود
        return
    if not igstats.enabled() or not wt_brain.enabled():
        return
    import poller
    now = clock.tehran_now()
    if now.weekday() != 5 or not poller._in_shift(now):  # فقط شنبه، داخلِ شیفت
        return
    wk = now.strftime("%Y-%W")
    if db.get_meta("last_ig_autoplan") == wk:
        return
    try:
        days = _days_until_friday(now)  # شنبه → کلِ هفته
        plan, made = await _build_and_assign_igplan(0, days)
        if not plan:
            return  # ساخت نشد (حتی با فالبک) → متا ست نمی‌شود تا دورِ بعد دوباره تلاش شود
        group = _workgroup()
        for ch in _igplan_messages(plan, days, made):  # خلاصه + هر روز یک پیامِ تمیز → گروهِ کار
            try:
                if group:
                    await app.bot.send_message(group, ch, parse_mode=ParseMode.HTML)
                else:
                    await _send_managers(app.bot, ch)
            except Exception:  # noqa: BLE001
                pass
        db.set_meta("last_ig_autoplan", wk)  # فقط پس از ساخت و ارسالِ موفق
        ig_uid = _ig_admin_uid()
        if ig_uid:
            try:
                await app.bot.send_message(ig_uid, "📅 برنامهٔ محتواییِ این هفته‌ات آماده شد — با /tasks ببین 💪")
            except Exception:  # noqa: BLE001
                pass
        print(f"[worktasks] پلنِ محتواییِ خودکارِ هفتگی ساخته شد ({_fa(made)} تسک) — به گروه ارسال شد.")
    except Exception as e:  # noqa: BLE001
        print(f"[worktasks] پلنِ خودکار ناموفق: {e!r}")


def _norm_handle(s):
    h = (s or "").strip().strip("@").strip("/").lower()
    if "instagram.com/" in h:
        h = h.split("instagram.com/")[-1].split("/")[0].split("?")[0]
    return h.strip().strip("@")


async def cmd_rivals(update, context):
    """مدیریت و بنچمارکِ رقبای اینستاگرام (مدیر یا ادمینِ اینستاگرام).

    /rivals            → بنچمارکِ رقبا
    /rivals add id ... → افزودن   ·   /rivals rm id → حذف   ·   /rivals list → فهرست
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not (_is_admin(user.id) or user.id == _ig_admin_uid()):
        return
    args = context.args or []
    op = args[0].lower() if args else ""
    if op in ("add", "اضافه", "+"):
        handles = [h for h in (_norm_handle(x) for x in args[1:]) if h]
        added = sum(1 for h in handles if db.rival_add(h, user.id, user.full_name))
        hs = db.rivals()
        await msg.reply_text(f"✅ {_fa(added)} رقیب اضافه شد. کلِ رقبا ({_fa(len(hs))}):\n"
                             + "، ".join("@" + h for h in hs))
        return
    if op in ("rm", "remove", "del", "حذف", "-"):
        rmd = sum(1 for x in args[1:] if db.rival_remove(_norm_handle(x)))
        await msg.reply_text(f"🗑️ {_fa(rmd)} رقیب حذف شد. باقی‌مانده: {_fa(len(db.rivals()))}")
        return
    if op in ("list", "لیست"):
        hs = db.rivals()
        await msg.reply_text(f"🏁 رقبا ({_fa(len(hs))}):\n" + ("، ".join("@" + h for h in hs) or "—"))
        return
    if not db.rivals():
        await msg.reply_text("هنوز رقیبی اضافه نشده. آیدیِ پیجِ رقبا را بده؛ مثال:\n"
                             "<code>/rivals add page_one page_two</code>", parse_mode=ParseMode.HTML)
        return
    wait = await msg.reply_text("🏁 در حال آماده‌سازیِ بنچمارکِ رقبا…")
    rep = await igstats.rivals_report()
    await wait.edit_text(igstats.format_rivals(rep), parse_mode=ParseMode.HTML)


async def cmd_setigadmin(update, context):
    """ثبتِ «ادمینِ اینستاگرام» تا آنالیزِ پیج در ارزیابیِ او لحاظ شود (فقط مدیر). ریپلای/منشن."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not _is_admin(user.id):
        await msg.reply_text("فقط برای مدیران است.")
        return
    target = _target_user(msg)
    if not target:
        await msg.reply_text("روی پیامِ ادمینِ اینستاگرام ریپلای بزن و /setigadmin بفرست (یا او را منشن کن).")
        return
    db.set_meta("ig_admin_uid", str(target[0]))
    _seen_id(target[0], target[1])
    await msg.reply_text(f"✅ «{target[1]}» به‌عنوانِ ادمینِ اینستاگرام ثبت شد؛ در ارزیابیِ روزانه‌اش، آمارِ واقعیِ پیج (رشد/ریچ/فالو) صحت‌سنجی می‌شود.")


async def cmd_linkwp(update, context):
    """لینکِ یک پرسنل به کاربرِ وردپرس (فقط مدیر). روی پیامِ پرسنل ریپلای بزن و /linkwp بفرست →
    لیستِ کاربرانِ وردپرس دکمه‌ای می‌آید تا انتخاب کنی. (حالتِ دستی: /linkwp <آیدی> هم کار می‌کند.)"""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not _is_admin(user.id):
        await msg.reply_text("فقط برای مدیران است.")
        return
    target = _target_user(msg)
    if not target:
        await msg.reply_text("روی پیامِ پرسنلِ موردِنظر ریپلای بزن و /linkwp بفرست (یا او را منشن کن).")
        return
    _seen_id(target[0], target[1])
    args = context.args or []
    if args and args[0].isdigit():  # حالتِ دستیِ سریع (سازگاریِ عقب)
        db.set_meta(f"wp_link:{target[0]}", args[0])
        await msg.reply_text(f"✅ «{target[1]}» به کاربرِ وردپرسِ {_fa(int(args[0]))} لینک شد.")
        return
    # حالتِ انتخابی: کاربرانِ وردپرس را از /agents دکمه‌ای نشان بده
    try:
        agents = await crm.get_agents()
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"دریافتِ لیستِ کاربرانِ سایت ناموفق: {type(e).__name__}. می‌توانی دستی بزنی: /linkwp <آیدی>")
        return
    if not agents:
        await msg.reply_text("کاربرِ سایتی یافت نشد. آیدی را از /agents ببین و دستی بزن: /linkwp <آیدی>")
        return
    rows = [[InlineKeyboardButton(f"{a.get('display_name', '?')} (#{a.get('user_id')})",
                                  callback_data=f"wt:linkwp:{target[0]}:{a.get('user_id')}")]
            for a in agents[:25] if a.get("user_id")]
    await msg.reply_text(
        f"کدام کاربرِ وردپرس به «{target[1]}» وصل شود؟ انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows))


# ============================================================
# نسخهٔ عملیاتیِ اصلی — چرخهٔ تسک، صحت‌سنجی، تسکِ سایت/اینستاگرام، گزارش (Core Operational Release)
# همه پشتِ feature flag؛ flag خاموش = رفتارِ دقیقاً فعلی (open/done). taskservice تنها نویسنده می‌ماند.
# ============================================================
import wt_verify  # noqa: E402


def _lc_enabled() -> bool:
    return bool(getattr(config, "WT_LIFECYCLE_ENABLED", False))


def _website_assignee() -> int:
    """مسئولِ سایت: اول configِ قطعی (WT_WEBSITE_ASSIGNEE_ID)، وگرنه fallbackِ metaِ موجود (اولین wp_link‌شده)."""
    cid = int(getattr(config, "WT_WEBSITE_ASSIGNEE_ID", 0) or 0)
    if cid:
        return cid
    with db._lock:  # fallback: تنها پرسنلِ لینک‌شده به وردپرس (اگر یکی بود)
        rows = db._conn.execute("SELECT key FROM meta WHERE key LIKE 'wp_link:%'").fetchall()
    if len(rows) == 1:
        try:
            return int(rows[0][0].split(":", 1)[1])
        except (ValueError, IndexError):
            return 0
    return 0


def _instagram_assignee() -> int:
    """مسئولِ اینستاگرام: اول configِ قطعی (WT_INSTAGRAM_ASSIGNEE_ID)، وگرنه fallbackِ metaِ موجود (ig_admin_uid)."""
    cid = int(getattr(config, "WT_INSTAGRAM_ASSIGNEE_ID", 0) or 0)
    return cid or _ig_admin_uid()


def _lifecycle_ctx(actor_id, operation, idem=""):
    """MutationContext برای عملیاتِ چرخه؛ actor از تلگرام/سیستم (نه LLM)."""
    if not actor_id:
        return taskservice.system_context(operation, idempotency_key=idem)
    return taskservice.MutationContext(actor_id=int(actor_id), actor_role=_role_of(actor_id), source="telegram",
                                       operation=operation, idempotency_key=idem)


_INACTIVE = taskservice.MutationResult("unauthorized", detail="inactive personnel")


# ---------- اکشن‌های پرسنل روی تسکِ خودش ----------
def lifecycle_start(task_id, actor_id, idem=""):
    if _staff_blocked(actor_id):
        return _INACTIVE
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:start:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "in_progress")


def lifecycle_block(task_id, actor_id, reason, idem=""):
    if _staff_blocked(actor_id):
        return _INACTIVE
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:block:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "blocked", reason=reason)


def lifecycle_resume(task_id, actor_id, idem=""):
    if _staff_blocked(actor_id):
        return _INACTIVE
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:resume:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "in_progress")


def lifecycle_done(task_id, actor_id, note=None, idem=""):
    """اعلامِ «انجام شد» توسطِ پرسنل. هدف بر اساسِ verification_mode: none→verified، manager/automatic→claimed."""
    if _staff_blocked(actor_id):
        return _INACTIVE, None
    t = taskservice.get_task(task_id)
    if not t:
        return taskservice.MutationResult("not_found", task_id=task_id), None
    target = taskservice.resolve_done_target(t["verification_mode"])
    if int(actor_id or 0) in _OPERATOR_IDS:  # اپراتور: «انجام شد»‌ش مستقیم تأیید و بسته می‌شود (بدونِ صحت‌سنجیِ فروش‌محور)
        target = "verified_done"
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:done:{actor_id}:{task_id}")
    res = taskservice.transition_task(ctx, task_id, target, completion_note=note)
    return res, target


# ---------- اکشن‌های مدیر ----------
def mgr_approve(task_id, actor_id, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:approve:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "verified_done")


def mgr_reopen(task_id, actor_id, reason, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:reopen:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "reopened", reason=reason)


def mgr_cancel(task_id, actor_id, reason, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_transition", idem or f"tg:cancel:{actor_id}:{task_id}")
    return taskservice.transition_task(ctx, task_id, "cancelled", reason=reason)


def mgr_reassign(task_id, actor_id, new_uid, new_name, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_reassign", idem or f"tg:reassign:{actor_id}:{task_id}:{new_uid}")
    return taskservice.reassign_task(ctx, task_id, new_uid, new_name)


def mgr_set_priority(task_id, actor_id, priority, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_set_priority", idem or f"tg:prio:{actor_id}:{task_id}:{priority}")
    return taskservice.set_priority(ctx, task_id, priority)


def mgr_set_deadline(task_id, actor_id, deadline_ts, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_set_deadline", idem or f"tg:dl:{actor_id}:{task_id}:{deadline_ts}")
    return taskservice.set_deadline(ctx, task_id, deadline_ts)


def mgr_set_vmode(task_id, actor_id, mode, idem=""):
    ctx = _lifecycle_ctx(actor_id, "task_set_verification_mode", idem or f"tg:vm:{actor_id}:{task_id}:{mode}")
    return taskservice.set_verification_mode(ctx, task_id, mode)


# ---------- ساختِ تسکِ سایت/اینستاگرام (metadataِ ساختاریافته + idempotency) ----------
def _feature_task(source_feature, assignee_id, assignee_name, assigner_id, text, *, mode, rule=None,
                  priority="normal", deadline_ts=None, entity_type="", entity_id="", operation="",
                  event_id="") -> int:
    """ساختِ تسکِ staff با source_feature و (اختیاری) verify_rule. کلیدِ idempotency از entity+operation(+event).

    خروجی: idِ تسک، یا -1 (dup/رد). rule اگر داده شود در کد allowlist می‌شود (نه LLM).
    برای «رویدادِ واقعیِ جدید» پس از تکمیل/لغوِ تسکِ قبلی، event_idِ متفاوت بده تا تسکِ تازه ساخته شود."""
    if rule is not None:
        ok, err = wt_verify.validate_rule(rule)
        if not ok:
            print(f"[worktasks] verify_rule نامعتبر رد شد: {err}")
            return -1
    idem = f"{source_feature}:{entity_type}:{entity_id}:{operation}" + (f":{event_id}" if event_id else "")
    ctx = taskservice.MutationContext(
        actor_id=int(assigner_id) if assigner_id else taskservice.SYSTEM_ACTOR_ID,
        actor_role=_role_of(assigner_id), source=("telegram" if assigner_id else "system"),
        operation="task_create", idempotency_key=idem)
    lc = "open" if _lc_enabled() else None
    res = taskservice.create_task(
        ctx, assignee_id, assignee_name, "🤖 سیستم" if not assigner_id else _staff_name(assigner_id) or "مدیر", text,
        source_key=idem, task_kind="staff", lifecycle_state=lc, verification_mode=mode, priority=priority,
        deadline_ts=deadline_ts, source_feature=source_feature,
        verify_rule_json=wt_verify.dumps_rule(rule) if rule else None)
    if res.status in ("applied",) or (res.status == "duplicate" and res.task_id):
        return res.task_id
    return -1


def create_website_task(text, *, entity_type, entity_id, operation, verify_rule=None, mode="manager",
                        priority="normal", deadline_ts=None, assignee_id=None, assigner_id=None,
                        event_id="") -> int:
    """تسکِ مرتبط با سایت برای «مسئولِ سایت». source_feature=website. metadataِ entity + ruleِ allowlist."""
    if not getattr(config, "WT_WEBSITE_TASKS_ENABLED", False):
        return -1
    aid = assignee_id or _website_assignee()
    if not aid:
        print("[worktasks] مسئولِ سایت map نشده — تسکِ سایت ساخته نشد.")
        return -1
    if mode == "automatic" and not getattr(config, "WT_AUTOMATIC_VERIFICATION_ENABLED", False):
        mode = "manager"  # صحت‌سنجیِ خودکار خاموش → به تأییدِ مدیر برگرد (رد/گم نمی‌شود)
    return _feature_task("website", aid, _staff_name(aid) or "مسئولِ سایت", assigner_id, text,
                         mode=mode, rule=verify_rule, priority=priority, deadline_ts=deadline_ts,
                         entity_type=entity_type, entity_id=entity_id, operation=operation, event_id=event_id)


def create_instagram_task(text, *, operation, entity_type="ig", entity_id="", verify_rule=None, mode="manager",
                          priority="normal", deadline_ts=None, assignee_id=None, assigner_id=None,
                          event_id="") -> int:
    """تسکِ انسانیِ اینستاگرام برای «مسئولِ اینستاگرام». source_feature=instagram (جدا از ig_planِ سیستمی)."""
    if not getattr(config, "WT_INSTAGRAM_TASKS_ENABLED", False):
        return -1
    aid = assignee_id or _instagram_assignee()
    if not aid:
        print("[worktasks] مسئولِ اینستاگرام map نشده — تسکِ اینستاگرام ساخته نشد.")
        return -1
    if mode == "automatic" and not getattr(config, "WT_AUTOMATIC_VERIFICATION_ENABLED", False):
        mode = "manager"
    return _feature_task("instagram", aid, _staff_name(aid) or "مسئولِ اینستاگرام", assigner_id, text,
                         mode=mode, rule=verify_rule, priority=priority, deadline_ts=deadline_ts,
                         entity_type=entity_type, entity_id=entity_id, operation=operation, event_id=event_id)


# ---------- ارکستریشنِ صحت‌سنجیِ خودکار (خارج از تراکنش) ----------
async def verify_and_apply(task_id, website=None, instagram=None) -> str:
    """اگر تسک claimed_done با mode=automatic بود: API را خارج از تراکنش می‌خواند و نتیجهٔ قطعی را اعمال می‌کند.

    positive → verified_done؛ negative/unavailable → همان claimed_done (رد/گم نمی‌شود). retry دوباره transition نمی‌سازد.
    """
    if not getattr(config, "WT_AUTOMATIC_VERIFICATION_ENABLED", False):
        return "disabled"
    t = taskservice.get_task(task_id)
    if not t or t["state"] != "claimed_done" or t["verification_mode"] != "automatic":
        return "skip"
    rule = wt_verify.loads_rule(t["verify_rule_json"])
    if not rule:
        return "no_rule"
    website = website or wt_verify.WebsiteAdapter()
    instagram = instagram or wt_verify.InstagramAdapter()
    res = await wt_verify.verify_rule(rule, website=website, instagram=instagram, cache={},
                                      timeout=float(getattr(config, "WT_VERIFY_TIMEOUT_SEC", 8)))
    if res.outcome == "positive":
        ctx = taskservice.system_context("task_transition",
                                         idempotency_key=f"verify:{task_id}:verified")
        taskservice.transition_task(ctx, task_id, "verified_done",
                                    verification_source=res.source, verification_ref=res.ref)
        return "verified"
    return res.outcome  # negative | unavailable → در claimed_done می‌ماند (مدیر می‌بیند)


# ---------- parserهای قطعیِ deadline/priority (بدونِ LLM) ----------
_PRIORITY_WORDS = {"normal": "normal", "عادی": "normal", "معمولی": "normal",
                   "high": "high", "مهم": "high", "بالا": "high",
                   "urgent": "urgent", "فوری": "urgent", "اورژانسی": "urgent"}


def parse_priority(text):
    return _PRIORITY_WORDS.get((text or "").strip().lower())


def parse_deadline(text) -> float | None:
    """ورودیِ سادهٔ فارسی/عددی → epochِ UTC (بر پایهٔ ساعتِ عملیاتیِ تهران). None اگر نامفهوم.

    پشتیبانی: «امروز [HH:MM]»، «فردا [HH:MM]»، «YYYY/MM/DD [HH:MM]» (شمسی)، «+Nd»، «+Nh».
    """
    import datetime
    s = (text or "").strip()
    if not s:
        return None
    now = clock.tehran_now()
    hh, mm = 23, 59

    def _epoch(dt_tehran):
        # تهران = UTC+3:30 → UTC epoch
        return (dt_tehran - datetime.timedelta(hours=3, minutes=30)).replace(tzinfo=datetime.timezone.utc).timestamp()

    m = None
    parts = s.split()
    # زمانِ اختیاری HH:MM در انتها
    if parts and ":" in parts[-1]:
        try:
            hh, mm = (int(x) for x in parts[-1].split(":")[:2])
            parts = parts[:-1]
        except ValueError:
            pass
    head = " ".join(parts).strip()
    if head in ("امروز", "today"):
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return _epoch(base)
    if head in ("فردا", "tomorrow"):
        base = (now + datetime.timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return _epoch(base)
    if head.startswith("+") and head[1:-1].isdigit() and head[-1] in ("d", "h"):
        n = int(head[1:-1])
        delta = datetime.timedelta(days=n) if head[-1] == "d" else datetime.timedelta(hours=n)
        return (clock.utcnow() + delta).replace(tzinfo=datetime.timezone.utc).timestamp()
    if "/" in head:  # شمسی YYYY/MM/DD
        try:
            y, mo, d = (int(x) for x in head.split("/")[:3])
            g = jdatetime.date(y, mo, d).togregorian()
            base = datetime.datetime(g.year, g.month, g.day, hh, mm)
            return _epoch(base)
        except Exception:
            return None
    return None


def is_overdue(t) -> bool:
    """overdue یک محاسبهٔ derived است (نه state جدید): deadline گذشته و تسک هنوز terminal نشده."""
    import datetime as _dt
    dl = t.get("deadline_ts")
    return bool(dl and dl < clock.utcnow().replace(tzinfo=_dt.timezone.utc).timestamp()
                and t.get("state") not in taskservice.TERMINAL_STATES)


# ---------- گزارشِ مدیریتیِ چرخه (deterministic؛ بدونِ LLM؛ بدونِ N+1) ----------
_STATE_FA = {"open": "شروع‌نشده", "in_progress": "در حالِ انجام", "blocked": "مسدود",
             "claimed_done": "منتظرِ تأیید", "verified_done": "تأییدشده", "reopened": "بازگشایی‌شده",
             "cancelled": "لغوشده"}
_SRC_FA = {"general": "عمومی", "website": "سایت", "instagram": "اینستاگرام"}


def lifecycle_counts() -> dict:
    """تجمیعِ قطعیِ تسک‌های staff بر پایهٔ چرخه: by_state, by_source, overdue, reopened, per_employee.

    یک query، سپس projection در کد (بدونِ N+1، بدونِ AI). مبنای همهٔ شمارش‌ها lifecycle_of(state,status) است.
    """
    import datetime as _dt
    nowe = clock.utcnow().replace(tzinfo=_dt.timezone.utc).timestamp()
    with db._lock:
        rows = db._conn.execute(
            "SELECT assignee_id, assignee_name, status, lifecycle_state, deadline_ts, source_feature, "
            "reopened_count, started_ts, claimed_done_ts, verified_ts "
            "FROM wt_tasks WHERE COALESCE(task_kind,'staff')='staff'").fetchall()
    by_state = {s: 0 for s in taskservice.LIFECYCLE_STATES}
    by_source = {s: 0 for s in taskservice.SOURCE_FEATURES}
    overdue = 0
    per_emp: dict = {}
    for aid, aname, status, lc, dl, src, reop, st_ts, cd_ts, vf_ts in rows:
        state = taskservice.lifecycle_of(lc, status)
        by_state[state] = by_state.get(state, 0) + 1
        src = src or "general"
        by_source[src] = by_source.get(src, 0) + 1
        od = bool(dl and dl < nowe and state not in taskservice.TERMINAL_STATES)
        if od:
            overdue += 1
        e = per_emp.setdefault(aid, {"name": aname, "active": 0, "completed": 0, "awaiting": 0,
                                     "overdue": 0, "reopened": 0, "start_to_claim": [], "claim_to_verify": []})
        if state in taskservice.TERMINAL_STATES:
            if state == "verified_done":
                e["completed"] += 1
        else:
            e["active"] += 1
        if state == "claimed_done":
            e["awaiting"] += 1
        if od:
            e["overdue"] += 1
        e["reopened"] += int(reop or 0)
        if st_ts and cd_ts and cd_ts >= st_ts:
            e["start_to_claim"].append(cd_ts - st_ts)
        if cd_ts and vf_ts and vf_ts >= cd_ts:
            e["claim_to_verify"].append(vf_ts - cd_ts)
    return {"by_state": by_state, "by_source": by_source, "overdue": overdue, "per_employee": per_emp}


def lifecycle_report_text() -> str:
    """کارتِ مدیریتیِ وضعیتِ واقعیِ کارها (بدونِ AI). گزارش بدونِ AI هم کامل و قابلِ استفاده است."""
    c = lifecycle_counts()
    bs, bsrc = c["by_state"], c["by_source"]
    lines = ["📋 <b>وضعیتِ کارها (چرخهٔ عملیاتی)</b>", ""]
    order = ["open", "in_progress", "blocked", "claimed_done", "reopened", "verified_done", "cancelled"]
    lines.append("• " + " · ".join(f"{_STATE_FA[s]}: {_fa(bs.get(s, 0))}" for s in order))
    lines.append(f"• ⏰ overdue: {_fa(c['overdue'])}")
    lines.append("• منابع → " + " · ".join(f"{_SRC_FA.get(s, s)}: {_fa(bsrc.get(s, 0))}" for s in
                                            ("general", "website", "instagram")))
    lines.append("")
    for aid, e in sorted(c["per_employee"].items(), key=lambda kv: -(kv[1]["active"] + kv[1]["awaiting"])):
        if not (e["active"] or e["awaiting"] or e["completed"] or e["overdue"]):
            continue
        seg = (f"👤 <b>{html.escape(e['name'] or str(aid))}</b> — فعال {_fa(e['active'])} · "
               f"منتظرِ تأیید {_fa(e['awaiting'])} · تکمیل {_fa(e['completed'])} · "
               f"overdue {_fa(e['overdue'])} · بازگشایی {_fa(e['reopened'])}")
        lines.append(seg)
    return "\n".join(lines)


def pending_approval_text() -> str:
    """فهرستِ تسک‌های منتظرِ تأییدِ مدیر (claimed_done)."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, assignee_name, text, source_feature, verification_source, verification_ref "
            "FROM wt_tasks WHERE lifecycle_state='claimed_done' ORDER BY claimed_done_ts").fetchall()
    if not rows:
        return "✅ هیچ تسکی منتظرِ تأیید نیست."
    lines = ["🕵️ <b>منتظرِ تأییدِ مدیر</b>", ""]
    for tid, aname, text, src, vsrc, vref in rows:
        tag = f" [{_SRC_FA.get(src, src)}]" if src and src != "general" else ""
        vr = f" · صحت‌سنجی: {html.escape(vref)}" if vref else ""
        lines.append(f"• <code>#{tid}</code>{tag} {html.escape((text or '')[:60])} — {html.escape(aname or '')}{vr}")
    lines.append("\n<i>تأیید: /approve id · بازگشایی: /reopen id دلیل · لغو: /cancel id دلیل</i>")
    return "\n".join(lines)


# ---------- نوتیفیکیشنِ چرخه (WS19): مستقل از تراکنش، delivery ledger، پیش‌فرض خاموش ----------
async def notify_transition(bot, task_id, event, text):
    """پیامِ چرخه را ایمن می‌فرستد: کلیدِ منطقیِ ثابت، از delivery ledger، بدونِ meta-set قبل از send (ضدِ D-RG-01).

    شکستِ ارسال، state را rollback نمی‌کند (transition جدا از send است). پیش‌فرض خاموش تا فعال‌سازیِ صریح."""
    if not getattr(config, "WT_NEW_NOTIFICATIONS_ENABLED", False) or not bot:
        return
    group = _workgroup()
    if not group:
        return
    # همان الگوی امنِ مشترکِ D-RG-01؛ transition جدا از send است و شکستِ ارسال state را برنمی‌گرداند.
    await _guarded_send(f"wt_notif:{task_id}:{event}", "wt_transition_notif",
                        lambda: bot.send_message(group, text, parse_mode=ParseMode.HTML))


# ---------- دستورهای مدیر (deterministic؛ بدونِ LLM) ----------
def _args(msg):
    return (msg.text or "").split()[1:]


def _need_admin_lc(user):
    return _lc_enabled() and user and _is_admin(user.id)


async def cmd_approve(update, context):
    """/approve <id> — تأییدِ انجامِ تسک (claimed_done → verified_done). فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if not a or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /approve <شمارهٔ تسک>")
        return
    tid = int(a[0].lstrip("#"))
    res = mgr_approve(tid, user.id)
    await msg.reply_text("✅ تأیید شد." if res.status == "applied"
                         else ("قبلاً تأیید شده." if res.status == "noop" else f"انجام نشد ({res.status}: {res.detail})."))


async def cmd_reopen(update, context):
    """/reopen <id> <دلیل> — بازگشاییِ تسکِ رد‌شده. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if len(a) < 2 or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /reopen <شماره> <دلیل>")
        return
    res = mgr_reopen(int(a[0].lstrip("#")), user.id, " ".join(a[1:]))
    await msg.reply_text("🔄 بازگشایی شد." if res.status == "applied" else f"انجام نشد ({res.status}: {res.detail}).")


async def cmd_cancel(update, context):
    """/cancel <id> <دلیل> — لغوِ تسک. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if len(a) < 2 or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /cancel <شماره> <دلیل>")
        return
    res = mgr_cancel(int(a[0].lstrip("#")), user.id, " ".join(a[1:]))
    await msg.reply_text("🚫 لغو شد." if res.status == "applied" else f"انجام نشد ({res.status}: {res.detail}).")


async def cmd_reassign(update, context):
    """/reassign <id> (ریپلای/منشنِ فرد) — واگذاریِ مجدد. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if not a or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /reassign <شماره> + منشن/ریپلایِ فردِ جدید")
        return
    tgt = _resolve_target(msg, " ".join(a[1:]))
    if not tgt:
        await msg.reply_text("فردِ جدید را با منشن یا ریپلای مشخص کن.")
        return
    _seen_id(tgt[0], tgt[1])
    res = mgr_reassign(int(a[0].lstrip("#")), user.id, tgt[0], tgt[1])
    await msg.reply_text(f"↪️ به {tgt[1]} واگذار شد." if res.status == "applied"
                         else f"انجام نشد ({res.status}: {res.detail}).")


async def cmd_deadline(update, context):
    """/deadline <id> <امروز|فردا|YYYY/MM/DD [HH:MM]|+Nd|+Nh> — مهلت. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if len(a) < 2 or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /deadline <شماره> <امروز|فردا|۱۴۰۴/۰۵/۰۱ [۱۸:۰۰]|+۲d>")
        return
    dl = parse_deadline(" ".join(a[1:]))
    if dl is None:
        await msg.reply_text("زمان نامفهوم بود.")
        return
    res = mgr_set_deadline(int(a[0].lstrip("#")), user.id, dl)
    await msg.reply_text("⏰ مهلت ثبت شد." if res.status == "applied" else f"انجام نشد ({res.status}).")


async def cmd_priority(update, context):
    """/priority <id> <عادی|مهم|فوری> — اولویت. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    p = parse_priority(a[1]) if len(a) >= 2 else None
    if len(a) < 2 or not a[0].lstrip("#").isdigit() or not p:
        await msg.reply_text("فرمت: /priority <شماره> <عادی|مهم|فوری>")
        return
    res = mgr_set_priority(int(a[0].lstrip("#")), user.id, p)
    await msg.reply_text("🎚️ اولویت ثبت شد." if res.status == "applied" else f"انجام نشد ({res.status}).")


async def cmd_vmode(update, context):
    """/vmode <id> <none|manager|automatic> — حالتِ صحت‌سنجی. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    a = _args(msg)
    if len(a) < 2 or not a[0].lstrip("#").isdigit() or a[1] not in taskservice.VERIFICATION_MODES:
        await msg.reply_text("فرمت: /vmode <شماره> <none|manager|automatic>")
        return
    mode = a[1]
    if mode == "automatic" and not getattr(config, "WT_AUTOMATIC_VERIFICATION_ENABLED", False):
        await msg.reply_text("صحت‌سنجیِ خودکار (flag) خاموش است.")
        return
    res = mgr_set_vmode(int(a[0].lstrip("#")), user.id, mode)
    await msg.reply_text("🔧 حالتِ صحت‌سنجی ثبت شد." if res.status == "applied" else f"انجام نشد ({res.status}).")


async def cmd_pending(update, context):
    """/pending — تسک‌های منتظرِ تأیید. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    await msg.reply_text(pending_approval_text(), parse_mode=ParseMode.HTML)


async def cmd_lcreport(update, context):
    """/board — کارتِ وضعیتِ چرخهٔ کارها. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _need_admin_lc(user):
        return
    await msg.reply_text(lifecycle_report_text(), parse_mode=ParseMode.HTML)


# ============================================================
# مدیریتِ سادهٔ پرسنل + حضور + حقوق (Core HR) — همه پشتِ flag، دستورهای مدیر
# ============================================================
_pending_salary: dict = {}  # actor_id → {pid, name, method, amount}


def _is_primary_admin(uid) -> bool:
    pid = getattr(config, "WT_PRIMARY_ADMIN_ID", 0)
    return (int(uid) == int(pid)) if pid else _is_admin(uid)


def _hr_admin(user) -> bool:
    return bool(getattr(config, "WT_PERSONNEL_ENABLED", False) and user and _is_admin(user.id))


def _payroll_pv(msg, user) -> bool:
    # وابستگی: payroll بدونِ personnel فعال نمی‌شود (fail-closed). فقط پیویِ مدیرِ اصلی.
    return bool(getattr(config, "WT_SIMPLE_PAYROLL_ENABLED", False) and getattr(config, "WT_PERSONNEL_ENABLED", False)
                and user and _is_primary_admin(user.id)
                and msg and getattr(getattr(msg, "chat", None), "type", "") == "private")


def _resolve_personnel_arg(arg):
    a = (arg or "").strip().lstrip("#")
    if a.isdigit():
        return wt_hr.get_personnel(int(a))
    ms = wt_hr.find_personnel_by_name(a)
    return ms[0] if len(ms) == 1 else None


async def cmd_personnel(update, context):
    """/personnel — فهرستِ پرسنل (فقط مدیر)."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    rows = wt_hr.list_personnel()
    if not rows:
        await msg.reply_text("هنوز پرسنلی ثبت نشده. با /addstaff اضافه کن.")
        return
    lines = ["👥 <b>پرسنل</b>", ""]
    for p in rows:
        st = "فعال ✅" if p["active"] else "غیرفعال ⛔"
        sal = f" · {wt_hr.method_fa(p['salary_method'])}" if p["salary_method"] else ""
        lines.append(f"• <code>#{p['id']}</code> {html.escape(p['name'])} — {st}"
                     + (f" · {html.escape(p['title'])}" if p["title"] else "") + sal)
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_addstaff(update, context):
    """/addstaff نام — افزودنِ پرسنل (اختیاری: ریپلای/منشن برای اتصالِ تلگرام)."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    a = _args(msg)
    if not a:
        await msg.reply_text("فرمت: /addstaff نامِ پرسنل  (برای اتصالِ تلگرام، روی پیامِ او ریپلای کن یا منشن بزن)")
        return
    name = " ".join(a)
    tgt = _resolve_target(msg, "")
    tg_uid = tgt[0] if tgt else None
    pid = wt_hr.add_personnel(user.id, name, tg_user_id=tg_uid)
    if pid > 0:
        await msg.reply_text(f"✅ پرسنل «{html.escape(name)}» با شناسهٔ <code>#{pid}</code> اضافه شد"
                             + (f" و به تلگرامِ {tgt[1]} وصل شد." if tgt else "."), parse_mode=ParseMode.HTML)
    else:
        await msg.reply_text("افزودن ناموفق بود (نام خالی؟).")


async def cmd_editstaff(update, context):
    """/editstaff <id> <name|title> <مقدار> — ویرایشِ نام یا بخش."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    a = _args(msg)
    if len(a) < 3 or not a[0].lstrip("#").isdigit() or a[1] not in ("name", "title", "نام", "بخش"):
        await msg.reply_text("فرمت: /editstaff <شماره> <name|title> <مقدار>")
        return
    pid = int(a[0].lstrip("#"))
    val = " ".join(a[2:])
    field = "name" if a[1] in ("name", "نام") else "title"
    ok = wt_hr.edit_personnel(user.id, pid, **{field: val})
    await msg.reply_text("✅ ثبت شد." if ok else "پرسنل یافت نشد.")


async def cmd_deactivatestaff(update, context):
    """/deactivatestaff <id> — غیرفعال‌سازی (بدونِ حذفِ سخت؛ سابقه حفظ)."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    a = _args(msg)
    if not a or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /deactivatestaff <شماره>")
        return
    pid = int(a[0].lstrip("#"))
    p = wt_hr.get_personnel(pid)
    if wt_hr.set_active(user.id, pid, False):
        openrows = _open_tasks(p["tg_user_id"] or 0) if (p and p["tg_user_id"]) else []
        tail = f"\n⚠️ {_fa(len(openrows))} تسکِ باز دارد که به مدیر نمایش داده می‌شود." if openrows else ""
        await msg.reply_text(f"⛔ پرسنل <code>#{pid}</code> غیرفعال شد. تسکِ جدید نمی‌گیرد؛ سابقه حفظ شد.{tail}",
                             parse_mode=ParseMode.HTML)
    else:
        await msg.reply_text("پرسنل یافت نشد.")


async def cmd_activatestaff(update, context):
    """/activatestaff <id> — فعال‌سازیِ مجدد."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    a = _args(msg)
    if not a or not a[0].lstrip("#").isdigit():
        await msg.reply_text("فرمت: /activatestaff <شماره>")
        return
    ok = wt_hr.set_active(user.id, int(a[0].lstrip("#")), True)
    await msg.reply_text("✅ فعال شد." if ok else "پرسنل یافت نشد.")


async def _show_salary_confirm(msg, name, method, amount):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید", callback_data="wt:salok"),
                                InlineKeyboardButton("✖️ لغو", callback_data="wt:salno")]])
    label = "نرخِ هر ساعت" if method == "hourly" else "مبلغ"
    await msg.reply_text(f"پرسنل: {html.escape(name)}\nروشِ محاسبه: {wt_hr.method_fa(method)}\n"
                         f"{label}: {wt_hr.fmt_money(amount)}\nتأیید / لغو", reply_markup=kb)


async def cmd_setsalary(update, context):
    """/setsalary <id> <fixed_monthly|hourly> <مبلغ> — یا در پیوی «حقوق …». نیازمندِ تأییدِ مدیرِ اصلی."""
    msg, user = update.effective_message, update.effective_user
    if not msg:
        return
    if not _payroll_pv(msg, user):
        await msg.reply_text("این دستور فقط در پیویِ مدیرِ اصلی و با فعال‌بودنِ حقوق کار می‌کند.")
        return
    a = _args(msg)
    if len(a) >= 3 and a[0].lstrip("#").isdigit() and a[1] in wt_hr.SALARY_METHODS:
        p = wt_hr.get_personnel(int(a[0].lstrip("#")))
        amount = wt_hr.parse_money(" ".join(a[2:]))
        if not p or amount is None:
            await msg.reply_text("پرسنل یا مبلغ نامعتبر.")
            return
        _pending_salary[user.id] = {"pid": p["id"], "name": p["name"], "method": a[1], "amount": amount}
        await _show_salary_confirm(msg, p["name"], a[1], amount)
        return
    await msg.reply_text("فرمت: /setsalary <شماره> <fixed_monthly|hourly> <مبلغ>\n"
                         "یا در پیوی بنویس: «حقوق علی ماهی ۳۰ میلیون تومان»")


async def cmd_setmonthhours(update, context):
    """/setmonthhours [YYYY-MM شمسی] <ساعت> — ساعتِ مبنای ماه (حقوقِ ثابتِ تناسبی). فقط مدیرِ اصلی در پیوی."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _payroll_pv(msg, user):
        return
    a = _args(msg)
    if not a:
        await msg.reply_text("فرمت: /setmonthhours [۱۴۰۵-۰۴] <ساعت>  (مثلاً: /setmonthhours 192)")
        return
    month = wt_hr.current_jmonth()
    if len(a) >= 2 and re.match(r"\d{3,4}-\d{1,2}", a[0].translate(_FA_NUM)):
        month = a[0].translate(_FA_NUM)
        hours = wt_hr.parse_money(a[1])
    else:
        hours = wt_hr.parse_money(a[-1])
    if hours is None:
        await msg.reply_text("ساعت نامعتبر.")
        return
    ok = wt_hr.set_month_base_hours(user.id, month, hours)
    await msg.reply_text(f"✅ ساعتِ مبنای ماهِ {month} = {_fa(hours)} ثبت شد." if ok else "ثبت نشد.")


async def cmd_attendance(update, context):
    """/attendance <id|نام> [YYYY-MM شمسی] — خلاصهٔ حضورِ ماه. فقط مدیر."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not _hr_admin(user):
        return
    a = _args(msg)
    if not a:
        await msg.reply_text("فرمت: /attendance <شماره|نام> [۱۴۰۵-۰۴]")
        return
    month = wt_hr.current_jmonth()
    if len(a) >= 2 and re.match(r"\d{3,4}-\d{1,2}", a[-1].translate(_FA_NUM)):
        month = a[-1].translate(_FA_NUM)
        a = a[:-1]
    p = _resolve_personnel_arg(" ".join(a))
    if not p:
        await msg.reply_text("پرسنل یافت نشد یا نام مبهم بود.")
        return
    if not p["tg_user_id"]:
        await msg.reply_text(f"{html.escape(p['name'])} به تلگرام وصل نشده — داده حضور ندارد.")
        return
    s = wt_hr.month_summary(p["tg_user_id"], month)
    await msg.reply_text(
        f"🕒 <b>حضورِ {html.escape(p['name'])} — {month}</b>\n"
        f"روزهای دارای حضور: {_fa(s['days'])}\nمجموعِ ساعتِ معتبر: {_fa(s['valid_hours'])}\n"
        f"اولین ورود: {s['first_in'] or '—'}\nآخرین خروج: {s['last_out'] or '—'}\n"
        f"ناقص (ورودِ بی‌خروج): {_fa(s['incomplete'])} · یتیم (خروجِ بی‌ورود): {_fa(s['orphan'])}",
        parse_mode=ParseMode.HTML)


def _payroll_text(pr) -> str:
    p = pr["personnel"]
    lines = [f"💰 <b>حقوقِ {html.escape(p['name'])} — {pr['month']}</b>", ""]
    lines.append(f"روشِ حقوق: {wt_hr.method_fa(pr['method'])}")
    lines.append(f"اولین ورود: {pr.get('first_in') or '—'} · آخرین خروج: {pr.get('last_out') or '—'}")
    lines.append(f"روزهای دارای حضور: {_fa(pr['days'])} · مجموعِ ساعتِ معتبر: {_fa(pr['hours'])}")
    lines.append(f"ناقص: {_fa(pr['incomplete'])} · یتیم: {_fa(pr['orphan'])}")
    if pr["method"] == "hourly":
        lines.append(f"نرخِ هر ساعت: {wt_hr.fmt_money(pr['amount'])}")
    else:
        lines.append(f"حقوقِ ثابت: {wt_hr.fmt_money(pr['amount'])}")
        lines.append(f"ساعتِ مبنای ماه: {_fa(pr['base_hours']) if pr['base_hours'] else 'ثبت‌نشده'}")
    if pr["status"] == "no_month_baseline":
        lines.append("⚠️ ساعتِ مبنای ماه ثبت نشده — حقوقِ تناسبی محاسبه نشد (حقوقِ ثابت و ساعات جدا نمایش داده شد).")
    elif pr["status"] == "no_attendance":
        lines.append("⚠️ حضوری برای این ماه ثبت نشده.")
    lines.append(f"مبلغِ محاسبه‌شده: {wt_hr.fmt_money(pr['computed'])}")
    if pr["adjustment"]:
        lines.append(f"اصلاحِ دستیِ مدیر: {wt_hr.fmt_money(pr['adjustment']['final_override'])} "
                     f"(دلیل: {html.escape(pr['adjustment'].get('reason') or '')})")
    lines.append(f"<b>مبلغِ نهایی: {wt_hr.fmt_money(pr['final'])}</b>")
    return "\n".join(lines)


async def cmd_payroll_summary(msg, month):
    rows = wt_hr.list_personnel(active=True)
    lines = [f"💰 <b>خلاصهٔ حقوقِ همه — {month}</b>", "", "نام | ساعت | روش | محاسبه‌شده | وضعیت"]
    for p in rows:
        pr = wt_hr.compute_payroll(p["id"], month)
        lines.append(f"{html.escape(p['name'])} | {_fa(pr['hours'])} | {wt_hr.method_fa(pr['method'])} | "
                     f"{wt_hr.fmt_money(pr['computed'])} | {pr['status']}")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_payroll(update, context):
    """/payroll [id|نام] [YYYY-MM شمسی] — گزارشِ حقوق. فقط مدیرِ اصلی در پیوی."""
    msg, user = update.effective_message, update.effective_user
    if not msg:
        return
    if not _payroll_pv(msg, user):
        await msg.reply_text("گزارشِ حقوق فقط در پیویِ مدیرِ اصلی و با فعال‌بودنِ حقوق در دسترس است.")
        return
    a = _args(msg)
    month = wt_hr.current_jmonth()
    if a and re.match(r"\d{3,4}-\d{1,2}", a[-1].translate(_FA_NUM)):
        month = a[-1].translate(_FA_NUM)
        a = a[:-1]
    if a:
        p = _resolve_personnel_arg(" ".join(a))
        if not p:
            await msg.reply_text("پرسنل یافت نشد.")
            return
        await msg.reply_text(_payroll_text(wt_hr.compute_payroll(p["id"], month)), parse_mode=ParseMode.HTML)
        return
    await cmd_payroll_summary(msg, month)


async def _hr_payroll_query(msg, user, text):
    """پرسشِ طبیعیِ حقوق در پیوی: «حقوق این ماه علی» / «گزارش حقوق این ماه» / «ساعات حضور رضا»."""
    month = wt_hr.current_jmonth()
    p = None
    for cand in wt_hr.list_personnel():
        if cand["name"] and cand["name"] in text:
            p = cand
            break
    if "حضور" in text or "ساعات" in text:
        if not p or not p["tg_user_id"]:
            await msg.reply_text("برای گزارشِ حضور، نامِ پرسنلِ متصل به تلگرام را مشخص کن.")
            return
        s = wt_hr.month_summary(p["tg_user_id"], month)
        await msg.reply_text(f"🕒 حضورِ {html.escape(p['name'])} — {month}: {_fa(s['valid_hours'])} ساعت "
                             f"({_fa(s['days'])} روز، ناقص {_fa(s['incomplete'])}، یتیم {_fa(s['orphan'])})",
                             parse_mode=ParseMode.HTML)
        return
    if p:
        await msg.reply_text(_payroll_text(wt_hr.compute_payroll(p["id"], month)), parse_mode=ParseMode.HTML)
    else:
        await cmd_payroll_summary(msg, month)


async def maybe_hr_private(update, context) -> bool:
    """پیویِ مدیرِ اصلی: «حقوق …» (ثبتِ حقوق یا پرسشِ گزارش). خروجی True اگر هندل شد."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or getattr(getattr(msg, "chat", None), "type", "") != "private":
        return False
    if not _is_primary_admin(user.id) or not getattr(config, "WT_SIMPLE_PAYROLL_ENABLED", False):
        return False
    text = (msg.text or "").strip()
    if not text.startswith("حقوق") and not text.startswith("ساعات حضور"):
        return False
    if text.startswith("ساعات حضور") or "گزارش" in text or "این ماه" in text or "حضور" in text:
        await _hr_payroll_query(msg, user, text)
        return True
    r = wt_hr.parse_salary_command(text)
    if not r.get("ok"):
        await msg.reply_text("متوجهِ مبلغ نشدم. مثال: «حقوق علی ماهی ۳۰ میلیون تومان»")
        return True
    if not r["method"]:
        await msg.reply_text("روشِ حقوق مشخص نیست؛ «ماهی/ثابت» یا «ساعتی» را بگو.")
        return True
    ms = wt_hr.find_personnel_by_name(r["name_hint"])
    if len(ms) != 1:
        await msg.reply_text(f"پرسنلِ «{html.escape(r['name_hint'])}» مبهم/نامشخص بود؛ دقیق‌تر بگو یا /personnel را ببین. "
                             "چیزی ذخیره نشد.")
        return True
    p = ms[0]
    _pending_salary[user.id] = {"pid": p["id"], "name": p["name"], "method": r["method"], "amount": r["amount"]}
    await _show_salary_confirm(msg, p["name"], r["method"], r["amount"])
    return True


# ---------- قطعِ همکاریِ سبک (مستقل از HR flag؛ بادوام، برگشت‌پذیر، سابقه‌حفظ) ----------
async def cmd_retire(update, context):
    """/retire (ریپلای/منشن/نام) — قطعِ همکاری: نه تسکِ جدید، نه یادآوری، نه در کارتِ عملکرد. سابقه حفظ؛ بازگشت با /unretire."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    tgt = _resolve_target(msg, " ".join(_args(msg)))
    if not tgt:
        await msg.reply_text("فردِ موردِ نظر را مشخص کن: روی پیامش ریپلای کن، یا «/retire @username» یا «/retire نام».")
        return
    uid, name = tgt
    _set_retired(user.id, uid, name, True)
    openrows = _open_tasks(uid)
    tail = (f"\n⚠️ <b>{_fa(len(openrows))} تسکِ باز</b> دارد؛ با /pending یا /tasks ببین و در صورتِ نیاز واگذار/ببند "
            f"(تسک‌ها خودکار بسته نشدند تا چیزی گم نشود)." if openrows else "")
    await msg.reply_text(
        f"⛔ «{html.escape(name)}» قطعِ همکاری شد.\nدیگر تسکِ جدید نمی‌گیرد، یادآوریِ گزارش نمی‌شود و در کارتِ عملکرد نمی‌آید. "
        f"سابقه‌اش کامل حفظ شد. برای بازگشت: <code>/unretire</code>.{tail}", parse_mode=ParseMode.HTML)


async def cmd_unretire(update, context):
    """/unretire (ریپلای/منشن/نام) — بازگشتِ همکاریِ پرسنلِ قطع‌شده."""
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    tgt = _resolve_target(msg, " ".join(_args(msg)))
    if not tgt:
        await msg.reply_text("فردِ موردِ نظر را مشخص کن: ریپلای/منشن/نام.")
        return
    uid, name = tgt
    _set_retired(user.id, uid, name, False)
    await msg.reply_text(f"✅ «{html.escape(name)}» دوباره فعال شد؛ از این پس تسک و یادآوری می‌گیرد.")
