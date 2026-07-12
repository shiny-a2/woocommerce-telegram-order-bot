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
import wt_brain

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
                    "ai_carryover TEXT", "ai_growth TEXT"):
            try:
                db._conn.execute(f"ALTER TABLE wt_reports ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        try:  # شرحِ وظایفِ هر پرسنل (برای اساینِ خودکارِ تسک‌های خزش)
            db._conn.execute("ALTER TABLE wt_staff ADD COLUMN role_desc TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # کلیدِ دسته‌ی مشکلِ خزش روی تسک (برای جلوگیری از تسکِ تکراریِ همان مشکل)
            db._conn.execute("ALTER TABLE wt_tasks ADD COLUMN source_key TEXT")
        except sqlite3.OperationalError:
            pass
        db._conn.commit()
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
                   name=excluded.name, last_ts=excluded.last_ts""",
            (uid, (username or "").lower() or None, name or str(uid), now, now),
        )
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
def _add_task(assignee_id, assignee_name, assigner_id, assigner_name, text, source_key=None) -> int:
    with db._lock:
        cur = db._conn.execute(
            """INSERT INTO wt_tasks(assignee_id, assignee_name, assigner_id, assigner_name, text, status,
                                    created_ts, source_key)
               VALUES (?,?,?,?,?, 'open', ?, ?)""",
            (assignee_id, assignee_name, assigner_id, assigner_name, text, time.time(), source_key),
        )
        db._conn.commit()
        return cur.lastrowid


def _open_crawl_keys() -> set:
    """کلیدهای دسته‌ای که همین حالا تسکِ بازِ خزش دارند (برای جلوگیری از تکرارِ همان مشکل)."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT DISTINCT source_key FROM wt_tasks "
            "WHERE status='open' AND source_key IS NOT NULL AND source_key<>''"
        ).fetchall()
    return {r[0] for r in rows}


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


def _open_tasks(user_id):
    with db._lock:
        return db._conn.execute(
            "SELECT id, text, assigner_name FROM wt_tasks WHERE assignee_id=? AND status='open' ORDER BY id",
            (user_id,),
        ).fetchall()


def _task_done(task_id, user_id) -> bool:
    with db._lock:
        cur = db._conn.execute(
            "UPDATE wt_tasks SET status='done', done_ts=? WHERE id=? AND assignee_id=? AND status='open'",
            (time.time(), task_id, user_id),
        )
        db._conn.commit()
        return cur.rowcount > 0


def _close_task_admin(tid):
    """مدیر هر تسکِ بازی را می‌بندد (مالکیت‌محور نیست). خروجی: (assignee_name, text) یا None."""
    with db._lock:
        r = db._conn.execute(
            "SELECT assignee_name, text FROM wt_tasks WHERE id=? AND status='open'", (int(tid),)).fetchone()
        if not r:
            return None
        db._conn.execute("UPDATE wt_tasks SET status='done', done_ts=? WHERE id=?", (time.time(), int(tid)))
        db._conn.commit()
    return r


def _edit_task(tid, new_text):
    """متنِ یک تسکِ باز را با دستورِ مدیر اصلاح می‌کند. خروجی: (assignee_name, old_text) یا None."""
    nt = (new_text or "").strip()
    if not nt:
        return None
    with db._lock:
        r = db._conn.execute(
            "SELECT assignee_name, text FROM wt_tasks WHERE id=? AND status='open'", (int(tid),)).fetchone()
        if not r:
            return None
        db._conn.execute("UPDATE wt_tasks SET text=? WHERE id=?", (nt, int(tid)))
        db._conn.commit()
    return r


def _add_report(user_id, name, text, kind="work") -> int:
    day = clock.tehran_now().strftime("%Y-%m-%d")
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO wt_reports(user_id, user_name, day, text, created_ts, kind) VALUES (?,?,?,?,?,?)",
            (user_id, name, day, text, time.time(), kind),
        )
        db._conn.commit()
        return cur.lastrowid


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
            if r.get("ok"):
                g = r.get("growth_1d")
                parts.append(
                    "کارِ واقعیِ اینستاگرام (آنالیزِ پیج): "
                    f"پستِ ۲۴ساعت={r.get('posts_24h', 0)}، پستِ ۷روز={r.get('posts_7d', 0)}، "
                    f"رشدِ فالوورِ امروز={('؟' if g is None else g)}، ریچِ پست‌های اخیر={r.get('total_reach', 0)}، "
                    f"فالوِ جذب‌شده از پست‌ها={r.get('total_follows_from_posts', 0)}، سیو={r.get('total_saves', 0)}")
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
    kind = _leave_kind(text)
    if kind:
        _add_report(user.id, user.full_name, text, kind=kind)
        if kind == "leave":
            await msg.reply_text("🌴 مرخصیت برای امروز ثبت شد. روزِ خوبی داشته باشی! (امروز ارزیابی و تسکی نداری.)")
        else:
            await msg.reply_text("📴 روزِ تعطیل برایت ثبت شد. (امروز ارزیابی و تسکی نداری.)")
        await maybe_send_perf_when_complete(msg.get_bot())
        return
    rid = _add_report(user.id, user.full_name, text)
    await msg.reply_text("📝 گزارشت ثبت شد. ممنون! 🙏")
    if wt_brain.enabled():
        asyncio.create_task(_ai_followup(msg, user, rid, text))
    else:
        await maybe_send_perf_when_complete(msg.get_bot())


async def _ai_followup(msg, user, rid, report_text):
    try:
        done, opent = _task_summaries(user.id)
        store = await _store_context()
        sc = await _staff_context(user.id)
        if sc:
            store = (store + "\n" + sc) if store else sc
        co = _carryover_context(user.id)
        if co:
            store = (store + "\n" + co) if store else co
        directives = _directives_block(user.id)
        qs = (await wt_brain.followup_questions(user.full_name, done, opent, report_text, store, directives)).strip()
        if qs:
            _awaiting_answers[user.id] = rid
            _store_report_field(rid, "ai_questions", qs)
            await msg.reply_text(f"🤖 برای ثبتِ عملکردت، لطفاً کوتاه پاسخ بده:\n\n{qs}")
        else:  # سؤالی نبود → گزارش همین‌جا تمام است
            await maybe_send_perf_when_complete(msg.get_bot())
    except Exception as e:
        print(f"[worktasks] ai_followup خطا: {e!r}")


async def _finalize_eval(msg, user, rid, answers):
    _store_report_field(rid, "ai_answers", answers)
    made = 0
    try:
        rep = _report_by_id(rid)
        done, opent = _task_summaries(user.id)
        qa = f"{rep.get('ai_questions', '')}\nپاسخِ کارمند: {answers}"
        store = await _store_context()
        sc = await _staff_context(user.id)
        if sc:
            store = (store + "\n" + sc) if store else sc
        co = _carryover_context(user.id)
        if co:
            store = (store + "\n" + co) if store else co
        directives = _directives_block(user.id)
        ev = await wt_brain.evaluate(user.full_name, done, opent, rep.get("text", ""), qa, store, directives)
        if ev:
            _store_eval(rid, ev)
            for t in sorted(ev.get("tasks") or [],
                            key=lambda x: {"high": 0, "med": 1, "low": 2}.get(x.get("priority"), 1))[:6]:
                _add_task(user.id, user.full_name, 0, "🤖 مدیرِ داخلی", t["label"])
                made += 1
    except Exception as e:
        print(f"[worktasks] finalize خطا: {e!r}")
    tail = f"\n📌 {_fa(made)} تسکِ پیگیری برای فردا برایت ثبت شد (با /tasks ببین)." if made else ""
    await msg.reply_text("✅ ممنون، ثبت شد. عملکردت برای مدیر لحاظ شد." + tail)
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


async def _handle_manager_reply(msg, user) -> None:
    """ریپلای مدیر روی پیامِ ربات را تفسیر و اجرا می‌کند: directive، ساخت/بستنِ تسک، اصلاح، «چشم مدیر»."""
    original = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    reply = (msg.text or "").strip()

    ctx_parts = []
    with db._lock:
        roster = db._conn.execute("SELECT name, username FROM wt_staff ORDER BY last_ts DESC LIMIT 30").fetchall()
    if roster:
        ctx_parts.append("اعضای تیم: " + "، ".join(f"{n}" + (f" (@{u})" if u else "") for n, u in roster))
    # هدفِ محتمل (منشن یا زنجیره‌ی ریپلای) + تسک‌های بازش با شماره — تا مغز بتواند تسکِ اشتباه را برای اصلاح/بستن بشناسد
    likely = _resolve_target(msg, "")
    if likely:
        rows = _open_tasks(likely[0])
        if rows:
            ctx_parts.append(f"تسک‌های بازِ {likely[1]}: " + "؛ ".join(f"#{tid} {t}" for tid, t, _a in rows))
    ctx = "\n".join(ctx_parts)

    r = await wt_brain.interpret_manager_reply(original, reply, ctx)
    if not r:
        await msg.reply_text("چشم مدیر، متوجه شدم. (تفسیرِ خودکار خطا داد؛ اگر دستورِ دائمی است، کوتاه و صریح دوباره بفرست.)")
        return

    done_lines = []
    target = _resolve_target(msg, r["target_hint"]) if r["scope"] == "user" else None

    if r["directive"]:
        if r["scope"] == "user" and target:
            _add_directive("user", target[0], r["directive"], user.id, user.full_name)
            done_lines.append(f"📌 دستورِ دائمی برای «{html.escape(target[1])}» ثبت شد و از این پس رعایت می‌شود.")
        else:
            _add_directive("global", None, r["directive"], user.id, user.full_name)
            done_lines.append("📌 دستورِ دائمی برای کلِ تیم ثبت شد و در ارزیابی‌های بعدی اعمال می‌شود.")

    if r["tasks"]:
        if target:
            _seen_id(target[0], target[1])
            for t in r["tasks"][:6]:
                _add_task(target[0], target[1], user.id, user.full_name, t)
            done_lines.append(f"🗂️ {_fa(len(r['tasks'][:6]))} تسک برای «{html.escape(target[1])}» ساخته شد.")
        else:
            done_lines.append("⚠️ تسک ساخته نشد چون پرسنلِ هدف مشخص نبود — روی پیامِ خودِ او ریپلای بزن یا منشنش کن.")

    for e in r.get("edits", []):
        rr = _edit_task(e["task_id"], e["new_text"])
        done_lines.append(
            f"✏️ تسک #{_fa(e['task_id'])} اصلاح شد → «{html.escape(e['new_text'][:70])}»" if rr
            else f"↪️ تسک #{_fa(e['task_id'])} برای اصلاح پیدا نشد (شاید بسته است).")

    for tid in r["close_task_ids"]:
        rr = _close_task_admin(tid)
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


# ---------- هوکِ پیامِ گروه (از on_text صدا زده می‌شود) ----------
async def on_group_message(update, context) -> bool:
    """در گروهِ گزارشِ کار: کشفِ پرسنل + ثبتِ تسک (منشنِ مدیر) + گزارشِ روزانه.

    اگر پیام مربوط به این ماژول بود True برمی‌گرداند (تا on_text ادامه ندهد).
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
    text = (msg.text or "").strip()

    # پاسخِ سؤالاتِ ارزیابیِ AI (بعد از گزارش)
    if user.id in _awaiting_answers:
        rid = _awaiting_answers.pop(user.id)
        await _finalize_eval(msg, user, rid, text)
        return True

    # اگر با دکمه‌ی «ثبتِ گزارش» منتظرِ متن بودیم، همین پیام گزارشِ روزانه است
    if user.id in _awaiting:
        if time.time() - _awaiting.pop(user.id, 0) <= _AWAIT_TTL:
            await _process_report(msg, user, text)
            return True

    # مدیر «تعطیل» اعلام می‌کند → تعطیلِ عمومیِ کلِ تیم برای امروز (نه یک شخص).
    # مدیر به‌طورِ طبیعی روی پیامِ ربات (مثلاً یادآوریِ گزارش) ریپلای می‌زند. دستور اطاعت می‌شود
    # و برای خودِ مدیر هیچ گزارشی ثبت نمی‌شود.
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
        await _handle_manager_reply(msg, user)
        return True

    # گزارشِ روزانه: پیامی که با «گزارش» شروع شود
    if text.startswith("گزارش"):
        body = text[len("گزارش"):].lstrip(" :،-").strip() or text
        await _process_report(msg, user, body)
        return True

    # ثبتِ تسک: فقط مدیر، با منشن
    if not _is_admin(user.id):
        return False
    assignees = _mentioned_users(msg)
    if not assignees:
        return False
    for aid, aname in assignees:
        _seen_id(aid, aname)
        _add_task(aid, aname, user.id, user.full_name, text)
    who = "، ".join(a[1] for a in assignees)
    await msg.reply_text(f"🗂️ تسک برای {who} ثبت شد. (با /tasks قابلِ مشاهده و بستن است)")
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

    if action == "done":
        try:
            tid = int(parts[2])
        except (ValueError, IndexError):
            await _ans(q)
            return True
        if _task_done(tid, uid):
            await _ans(q, "✅ ثبت شد.")
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

    if action == "tasks":  # «تسک‌های من»
        await _ans(q)
        rows = _open_tasks(uid)
        body = _tasks_text(rows) if rows else "✅ تسکِ بازی نداری."
        try:
            await q.message.reply_text(body, parse_mode=ParseMode.HTML,
                                       reply_markup=_tasks_kb(rows) if rows else None)
        except Exception:
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
    lines = [f"🗂️ <b>تسک‌های بازِ تو</b> ({len(rows)}):", ""]
    for tid, text, assigner in rows:
        lines.append(f"• <code>#{tid}</code> — {text}  <i>(از {assigner or '—'})</i>")
    return "\n".join(lines)


def _tasks_kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ انجام شد #{tid}", callback_data=f"wt:done:{tid}")]
                                 for tid, _t, _a in rows])


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
        await msg.reply_text("✅ تسکِ بازی نداری.")
        return
    await msg.reply_text(_tasks_text(rows), parse_mode=ParseMode.HTML, reply_markup=_tasks_kb(rows))


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
    await msg.reply_text("🗂️ <b>مرکزِ گزارشِ کار</b>\nیکی را انتخاب کن 👇",
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
    workers = [(u, n) for u, n in workers if u not in admins]
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


async def maybe_report_reminder(app):
    """پایانِ شیفت (یک‌بار در روز): به پرسنلی که امروز گزارش نداده‌اند در گروه یادآوری کن (با دکمه)."""
    import poller  # واردسازیِ تنبل (پرهیز از حلقه)
    now = clock.tehran_now()
    end = poller._shift_end_hour(now)
    if end is None or now.hour < end:  # تعطیل یا پیش از پایانِ شیفت
        return
    today = now.strftime("%Y-%m-%d")
    if _is_holiday(today):  # تعطیلِ عمومیِ اعلام‌شده → یادآوری نکن
        return
    if db.get_meta("last_report_reminder") == today:
        return
    group = _workgroup()
    if not group:
        return
    db.set_meta("last_report_reminder", today)
    missing = workers_without_report(today)
    if not missing:
        return
    mentions = " ".join(f'<a href="tg://user?id={uid}">{html.escape(name)}</a>' for uid, name in missing)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📝 ثبتِ گزارش", callback_data="wt:report")]])
    try:
        await app.bot.send_message(
            group,
            "⏰ <b>یادآوریِ گزارشِ کارِ امروز</b>\nاین عزیزان هنوز گزارش نداده‌اند — لطفاً همین حالا ثبت کنید 👇\n" + mentions,
            parse_mode=ParseMode.HTML, reply_markup=kb)
        print(f"[worktasks] یادآوریِ گزارش به {len(missing)} نفر ارسال شد.")
    except Exception as e:
        print(f"[worktasks] یادآوری ناموفق: {e!r}")


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


async def maybe_manager_report(app):
    """پایانِ شیفت (یک‌بار در روز): گزارشِ عملکردِ روزانه دایرکت به مدیران."""
    import poller
    import telegram_io
    now = clock.tehran_now()
    end = poller._shift_end_hour(now)
    if end is None or now.hour < end:
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_perf_report") == today:
        return
    workers, _r, _o = _workers_and_reports(today)
    if not workers:
        return
    db.set_meta("last_perf_report", today)
    try:
        await telegram_io.send_to_managers(app, daily_perf_text(today), parse_mode="HTML")
        print("[worktasks] گزارشِ عملکردِ روزانه به مدیران ارسال شد.")
    except Exception as e:
        print(f"[worktasks] گزارشِ مدیر ناموفق: {e!r}")


async def _send_managers(bot, text):
    """ارسالِ مدیرـمحور با یک bot (نه app): REPORTS_CHAT_ID یا پیویِ تک‌تکِ ادمین‌ها."""
    targets = [config.REPORTS_CHAT_ID] if config.REPORTS_CHAT_ID else list(config.ADMIN_USER_IDS)
    for t in targets:
        try:
            await bot.send_message(t, text, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            print(f"[worktasks] ارسال به مدیر {t} ناموفق: {e!r}")


async def maybe_send_perf_when_complete(bot):
    """وقتی «آخرین گزارش‌دهنده» ثبت شد (همه‌ی پرسنل امروز گزارش دادند)، کارتِ عملکرد را به مدیران بفرست.

    یک‌بار در روز (گاردِ last_perf_report — با گزارشِ زمان‌بندی‌شده مشترک). اگر کسی هنوز در حالِ
    پاسخ به سؤالاتِ ارزیابی است، صبر می‌کند تا کارت ناقص نرود.
    """
    today = clock.tehran_now().strftime("%Y-%m-%d")
    if _is_holiday(today) or db.get_meta("last_perf_report") == today:
        return
    if _awaiting_answers:  # کسی هنوز در حالِ ارزیابی است → هنوز آخرین نفر تمام نشده
        return
    workers, _r, _o = _workers_and_reports(today)
    if not workers or workers_without_report(today):  # هنوز همه گزارش نداده‌اند
        return
    db.set_meta("last_perf_report", today)
    try:
        await _send_managers(bot, daily_perf_text(today))
        print("[worktasks] همه گزارش دادند → کارتِ عملکرد به مدیران ارسال شد.")
    except Exception as e:  # noqa: BLE001
        print(f"[worktasks] گزارشِ عملکردِ رویدادی ناموفق: {e!r}")


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


async def _run_crawl(actor_id, actor_name):
    """خزش + اساینِ خودکارِ تسک‌ها طبقِ شرحِ وظایف. خروجی: (متنِ گزارشِ HTML، تعدادِ اساین‌شده). مشترکِ /crawl و خزشِ خودکار."""
    import crawler
    issues, notes = await crawler.collect()  # هر issue = {"key","text"}
    lines = ["🔎 <b>خزشِ مشکلات</b>", ""]
    n_assigned = 0
    open_keys = _open_crawl_keys()
    fresh = [i for i in issues if (i.get("key") or "") not in open_keys]
    already = [i for i in issues if (i.get("key") or "") in open_keys]
    if not issues:
        lines.append("مشکلِ عملی‌ای پیدا نشد ✅")
    elif not fresh:
        lines.append(f"مشکلِ تازه‌ای نبود؛ {_fa(len(already))} مورد از قبل تسکِ باز دارند (تکرار نشد) ✅")
    else:
        staff = _staff_roles()
        routes = (await wt_brain.route_issues([i["text"] for i in fresh],
                                              [{"name": n, "role": d} for _u, n, d in staff])
                  if staff else [])
        name2uid = {n: u for u, n, d in staff}
        assigned, pending = [], []
        if routes:
            for a in routes:
                nm = a.get("assignee")
                key = _match_key(a.get("task_text", ""), fresh)
                if nm and nm in name2uid:
                    _add_task(name2uid[nm], nm, actor_id, actor_name, a["task_text"], source_key=key)
                    assigned.append(f"• {html.escape(a['task_text'])} → <b>{html.escape(nm)}</b>")
                else:
                    pending.append(f"• {html.escape(a['task_text'])}")
        else:
            pending = [f"• {html.escape(i['text'])}" for i in fresh]
        n_assigned = len(assigned)
        if assigned:
            lines.append(f"✅ <b>{_fa(len(assigned))} تسک خودکار سپرده شد</b> (طبقِ شرحِ وظایف):")
            lines += assigned
        if pending:
            if assigned:
                lines.append("")
            lines.append("🕗 <b>نیازِ اساینِ دستی</b> (مسئولش مشخص نبود):")
            lines += pending
            lines.append("<i>برای سپردن، روی همین پیام ریپلای بزن و بگو «به {نام} بده».</i>")
        if already:
            lines.append("")
            lines.append(f"🔁 <i>{_fa(len(already))} مشکل از قبل تسکِ باز دارد؛ دوباره ساخته نشد.</i>")
    if notes:
        lines += ["", "⚠️ " + "؛ ".join(html.escape(n) for n in notes)]
    return "\n".join(lines), n_assigned


async def cmd_crawl(update, context):
    """خزشِ ملایمِ مشکلات و اساینِ خودکار به پرسنلِ مسئول (فقط مدیر، ضدبلاک)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not _is_admin(user.id):
        return
    await msg.reply_text("🔎 در حال خزشِ ملایم… (چند لحظه)")
    try:
        text, _n = await _run_crawl(user.id, user.full_name)
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"خزش ناموفق: {type(e).__name__}")
        return
    await msg.reply_text(text, parse_mode=ParseMode.HTML)


async def maybe_auto_crawl(app):
    """خزشِ خودکارِ اولِ شیفت (یک‌بار در روز): مشکلات را پیدا، خودکار به مسئول‌ها می‌سپارد و
    تسک‌ها را در «گروهِ کار» درج می‌کند تا تیم اولِ شیفت ببیند (فالبک: پیویِ مدیران).

    ضدبلاک: فقط یک‌بار در روز و از همان کلاینت‌های ملایمِ خزش (که به circuit-breaker احترام می‌گذارند).
    """
    import poller
    now = clock.tehran_now()
    if not poller._in_shift(now) or _is_holiday(now.strftime("%Y-%m-%d")):
        return
    today = now.strftime("%Y-%m-%d")
    if db.get_meta("last_auto_crawl") == today:
        return
    db.set_meta("last_auto_crawl", today)
    try:
        text, n = await _run_crawl(0, "🤖 خزشِ خودکار")
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
        print(f"[worktasks] خزشِ اولِ شیفت: {_fa(n)} تسک سپرده شد "
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
