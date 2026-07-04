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

import clock
import config
import db
import wt_brain

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(_FA)


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
        for col in ("ai_questions TEXT", "ai_answers TEXT", "ai_score INTEGER", "ai_summary TEXT", "ai_flags TEXT"):
            try:
                db._conn.execute(f"ALTER TABLE wt_reports ADD COLUMN {col}")
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


# ---------- تسک‌ها ----------
def _add_task(assignee_id, assignee_name, assigner_id, assigner_name, text) -> int:
    with db._lock:
        cur = db._conn.execute(
            """INSERT INTO wt_tasks(assignee_id, assignee_name, assigner_id, assigner_name, text, status, created_ts)
               VALUES (?,?,?,?,?, 'open', ?)""",
            (assignee_id, assignee_name, assigner_id, assigner_name, text, time.time()),
        )
        db._conn.commit()
        return cur.lastrowid


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


def _add_report(user_id, name, text) -> int:
    day = clock.tehran_now().strftime("%Y-%m-%d")
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO wt_reports(user_id, user_name, day, text, created_ts) VALUES (?,?,?,?,?)",
            (user_id, name, day, text, time.time()),
        )
        db._conn.commit()
        return cur.lastrowid


# ---------- ارزیابیِ AI (مغزِ ۵.۵): گزارش → سؤال → پاسخ → نمره ----------
_awaiting_answers: dict[int, int] = {}  # user_id → report_id (منتظرِ پاسخِ سؤالاتِ ارزیابی)


def _today_start() -> float:
    now = clock.tehran_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return time.time() - max(0.0, (now - start).total_seconds())


def _task_summaries(user_id):
    """متنِ کوتاهِ تسک‌های انجام‌شده‌ی امروز و تسک‌های باز (برای مغز)."""
    start = _today_start()
    with db._lock:
        done = db._conn.execute(
            "SELECT text FROM wt_tasks WHERE assignee_id=? AND status='done' AND done_ts>=?", (user_id, start)).fetchall()
        opent = db._conn.execute(
            "SELECT text FROM wt_tasks WHERE assignee_id=? AND status='open'", (user_id,)).fetchall()
    return ("؛ ".join(r[0] for r in done) or "—", "؛ ".join(r[0] for r in opent) or "—")


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


def _store_eval(rid, ev):
    with db._lock:
        db._conn.execute(
            "UPDATE wt_reports SET ai_score=?, ai_summary=?, ai_flags=? WHERE id=?",
            (ev.get("score"), ev.get("summary", ""), " | ".join(ev.get("flags") or []), rid))
        db._conn.commit()


async def _process_report(msg, user, text) -> None:
    """گزارش را ذخیره می‌کند، تشکر می‌کند، و اگر مغز فعال بود سؤالِ پیگیرانه می‌پرسد."""
    rid = _add_report(user.id, user.full_name, text)
    await msg.reply_text("📝 گزارشت ثبت شد. ممنون! 🙏")
    if wt_brain.enabled():
        asyncio.create_task(_ai_followup(msg, user, rid, text))


async def _ai_followup(msg, user, rid, report_text):
    try:
        done, opent = _task_summaries(user.id)
        qs = (await wt_brain.followup_questions(user.full_name, done, opent, report_text)).strip()
        if qs:
            _awaiting_answers[user.id] = rid
            _store_report_field(rid, "ai_questions", qs)
            await msg.reply_text(f"🤖 برای ثبتِ عملکردت، لطفاً کوتاه پاسخ بده:\n\n{qs}")
    except Exception as e:
        print(f"[worktasks] ai_followup خطا: {e!r}")


async def _finalize_eval(msg, user, rid, answers):
    _store_report_field(rid, "ai_answers", answers)
    try:
        rep = _report_by_id(rid)
        done, opent = _task_summaries(user.id)
        qa = f"{rep.get('ai_questions', '')}\nپاسخِ کارمند: {answers}"
        ev = await wt_brain.evaluate(user.full_name, done, opent, rep.get("text", ""), qa)
        if ev:
            _store_eval(rid, ev)
    except Exception as e:
        print(f"[worktasks] finalize خطا: {e!r}")
    await msg.reply_text("✅ ممنون، ثبت شد. عملکردت برای مدیر لحاظ شد.")


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
def daily_perf_text(day) -> str:
    """عملکردِ روزانه‌ی تیم: نمره‌ی AI + خلاصه + تسکِ باز + گزارش‌دهی."""
    workers, _reported, opencnt = _workers_and_reports(day)
    with db._lock:
        rows = db._conn.execute(
            "SELECT user_id, user_name, ai_score, ai_summary, ai_flags FROM wt_reports WHERE day=? ORDER BY id",
            (day,)).fetchall()
    latest = {r[0]: r for r in rows}  # آخرین گزارشِ هر کاربر در آن روز
    lines = [f"📊 <b>عملکردِ روزانه‌ی تیم — {day}</b>", ""]
    if not workers:
        lines.append("هنوز پرسنلی ثبت نشده.")
        return "\n".join(lines)
    scores = []
    for uid, name in workers:
        op = _fa(opencnt.get(uid, 0))
        r = latest.get(uid)
        if r:
            _u, _n, score, summ, flags = r
            sc = f"{_fa(score)}/۱۰۰" if score is not None else "ثبت‌شده"
            line = f"• <b>{html.escape(name)}</b> — نمره {sc} · تسکِ باز {op}"
            if summ:
                line += f"\n   └ {html.escape(summ)}"
            if flags:
                line += f"\n   ⚠️ {html.escape(flags)}"
            if score is not None:
                scores.append(score)
        else:
            line = f"• <b>{html.escape(name)}</b> — گزارش نداد ❌ · تسکِ باز {op}"
        lines.append(line)
    tail = f"گزارش‌دهنده: {_fa(len(latest))}/{_fa(len(workers))}"
    if scores:
        tail = f"میانگینِ نمره: {_fa(round(sum(scores) / len(scores)))}/۱۰۰ · " + tail
    lines += ["", tail]
    return "\n".join(lines)


def monthly_trend_text(month) -> str:
    """روندِ ماهانه‌ی هر نفر: میانگینِ نمره + تعدادِ روزهای گزارش‌داده."""
    admins = set(config.ADMIN_USER_IDS)
    with db._lock:
        rows = db._conn.execute(
            """SELECT user_id, user_name, COUNT(DISTINCT day), AVG(ai_score)
               FROM wt_reports WHERE day LIKE ? GROUP BY user_id ORDER BY AVG(ai_score) DESC""",
            (month + "%",)).fetchall()
    lines = [f"📈 <b>روندِ ماهانه — {month}</b>", ""]
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
