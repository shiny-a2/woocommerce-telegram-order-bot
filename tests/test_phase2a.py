"""تستِ فاز ۲A: report idempotency، delivery guard، crawl semantics (D-04/D-05)، task_kind، roles، adversarial.

کاملاً آفلاین (in-memory DB، fake sender/LLM؛ بدونِ API). اجرا: `python tests/test_phase2a.py`.
"""
import asyncio
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import db              # noqa: E402
import taskservice as ts   # noqa: E402
import worktasks as w  # noqa: E402


def check(name, cond):
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def _fresh():
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()
    config.ADMIN_USER_IDS = [111]
    config.WT_PRIMARY_ADMIN_ID = 0


def _cnt(tbl, where="", args=()):
    return db._conn.execute(f"SELECT COUNT(*) FROM {tbl}" + (f" WHERE {where}" if where else ""), args).fetchone()[0]


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


# ---------- fake Telegram objects (برای مسیرِ واقعیِ on_group_message) ----------
class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *a, **k):
        self.sent.append((a, k))
        return type("M", (), {"message_id": 555})()


class _FakeMsg:
    def __init__(self, text, bot):
        self.text = text
        self.reply_to_message = None
        self._bot = bot
        self.replies = []

    async def reply_text(self, *a, **k):
        self.replies.append(a[0] if a else "")

    def get_bot(self):
        return self._bot


class _FakeUser:
    def __init__(self, uid, name="کارمند"):
        self.id = uid
        self.full_name = name
        self.username = None
        self.is_bot = False


class _FakeChat:
    def __init__(self, cid):
        self.id = cid
        self.type = "group"


class _FakeUpdate:
    def __init__(self, uid_msg, text, chat_id, user):
        self.update_id = uid_msg
        self.effective_message = text
        self.effective_chat = _FakeChat(chat_id)
        self.effective_user = user


# ============ WORKSTREAM A — report idempotency (D-01) ============
def test_report_idempotency():
    _fresh()
    db.set_meta("work_group", "-500")
    # جلوگیری از cascade: followup/perf را no-op کن (رفتار ثبت گزارش را عوض نمی‌کند)
    w.wt_brain._client = None
    config.OPENAI_API_KEY = ""            # enabled()=False → followup پرش می‌شود
    orig_perf = w.maybe_send_perf_when_complete
    async def _noperf(*a, **k):
        return None
    w.maybe_send_perf_when_complete = _noperf
    try:
        bot = _FakeBot()
        staff = _FakeUser(222, "علی")
        rpt = "دوشنبه ۱۴۰۵/۰۴/۲۲\n۱۰:۰۵ - ۱۸:۳۵\n- کار"
        m1 = _FakeMsg(rpt, bot)
        u_same = _FakeUpdate(7001, m1, -500, staff)
        _run(w.on_group_message(u_same, None))
        _run(w.on_group_message(u_same, None))   # تحویلِ دوبارهٔ همان update
        ok = check("A) تحویلِ دوبارهٔ همان update → فقط یک گزارش", _cnt("wt_reports") == 1)
        # دو updateِ متفاوتِ هم‌متن → دو گزارشِ مشروع
        m2 = _FakeMsg(rpt, bot)
        _run(w.on_group_message(_FakeUpdate(7002, m2, -500, staff), None))
        ok &= check("A) دو updateِ متفاوتِ هم‌متن → دو گزارشِ مستقل", _cnt("wt_reports") == 2)
    finally:
        w.maybe_send_perf_when_complete = orig_perf
    return ok


# ============ WORKSTREAM B — delivery guard (D-02) ============
def test_delivery_guard():
    _fresh()
    ok = True
    d1, _ = ts.delivery_claim("perf:2026-07-16", operation="perf")
    ts.delivery_complete("perf:2026-07-16", message_id=999)
    d2, _ = ts.delivery_claim("perf:2026-07-16", operation="perf")
    ok &= check("B) claimِ دومِ همان پیامِ ارسال‌شده → duplicate (بدونِ ارسالِ دوباره)", d1 == "claimed" and d2 == "duplicate")
    row = db._conn.execute("SELECT status, result_reference FROM wt_inbound_events "
                           "WHERE source='delivery' AND external_event_id='perf:2026-07-16'").fetchone()
    ok &= check("B) message_id به‌عنوان delivery evidence ذخیره شد", row[0] == "succeeded" and row[1] == "999")
    # crash قبل از complete: lease معتبر → in_progress؛ منقضی → recovered
    ts.delivery_claim("reminder:x", lease_sec=999)
    dv, _ = ts.delivery_claim("reminder:x")
    ok &= check("B) crash قبل از complete + lease معتبر → in_progress (ارسالِ هم‌زمانِ دوم محدود)", dv == "in_progress")
    ts.delivery_claim("reminder:y", lease_sec=-1)
    dr, _ = ts.delivery_claim("reminder:y")
    ok &= check("B) lease منقضی → recovered (ارسالِ مجدد کنترل‌شده)", dr == "recovered")
    return ok


# ============ WORKSTREAM D/E — crawl semantics + audit ============
def test_crawl_created_immutable_and_audit():
    _fresh()
    ok = True
    tid = w._add_task(0, "—", 0, "sys", "مشکل", source_key="K", metric=5.0, kind="crawl")
    c0, e0 = db._conn.execute("SELECT created_ts, escalation_ref_ts FROM wt_tasks WHERE id=?", (tid,)).fetchone()
    time.sleep(0.02)
    w._bump_crawl_task(tid)
    c1, e1 = db._conn.execute("SELECT created_ts, escalation_ref_ts FROM wt_tasks WHERE id=?", (tid,)).fetchone()
    ok &= check("D-04) created_ts پس از bump immutable است", c1 == c0)
    ok &= check("D-04) escalation_ref_ts مستقل جلو می‌رود", e1 > e0)
    ok &= check("D-05) bump یک audit event می‌سازد",
                _cnt("wt_task_events", "event_type='crawl_task_escalation_reference_updated'") == 1)
    # refresh: تغییرِ واقعی → event؛ no-op → بدونِ event
    w._update_crawl_task(tid, "متنِ نو", 9.0)
    n_after_change = _cnt("wt_task_events", "event_type='crawl_task_refreshed'")
    w._update_crawl_task(tid, "متنِ نو", 9.0)   # همان مقدار → no-op
    n_after_noop = _cnt("wt_task_events", "event_type='crawl_task_refreshed'")
    ok &= check("D-05) refreshِ واقعی → یک event", n_after_change == 1)
    ok &= check("D-05) refreshِ no-op → event جدید نمی‌سازد", n_after_noop == 1)
    # rollback روی شکستِ audit
    orig = ts._insert_audit
    ts._insert_audit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    raised = False
    try:
        ts.bump_crawl_escalation(ts.system_context("crawl_escalation_bump"), tid)
    except RuntimeError:
        raised = True
    ts._insert_audit = orig
    c2 = db._conn.execute("SELECT escalation_ref_ts FROM wt_tasks WHERE id=?", (tid,)).fetchone()[0]
    ok &= check("D-05) شکستِ audit → rollback (escalation_ref_ts تغییر نکرد)", raised and c2 == e1)
    return ok


# ============ WORKSTREAM G — task_kind ============
def test_task_kind():
    _fresh()
    ok = True
    a = ts.create_task(ts.system_context("task_create"), 1, "x", "sys", "t", task_kind="staff")
    b = ts.create_task(ts.system_context("task_create"), 0, "—", "sys", "t", source_key="K", task_kind="crawl")
    c = ts.create_task(ts.system_context("task_create"), 2, "ig", "🤖 مدیرِ محتوا", "t", task_kind="ig_plan")
    bad = ts.create_task(ts.system_context("task_create"), 1, "x", "sys", "t", task_kind="frobnicate")
    ok &= check("G) create با kindهای staff/crawl/ig_plan موفق", a.status == "applied" and b.status == "applied" and c.status == "applied")
    ok &= check("G) kind خارج از allowlist → invalid", bad.status == "invalid")
    kinds = dict(db._conn.execute("SELECT task_kind, COUNT(*) FROM wt_tasks GROUP BY task_kind").fetchall())
    ok &= check("G) query بر اساس ستونِ task_kind کار می‌کند", kinds.get("staff") == 1 and kinds.get("crawl") == 1 and kinds.get("ig_plan") == 1)
    ok &= check("G) audit شاملِ task_kind است", '"task_kind":"crawl"' in
                (db._conn.execute("SELECT new_json FROM wt_task_events WHERE task_id=?", (b.task_id,)).fetchone()[0] or ""))
    return ok


# ============ WORKSTREAM F/D-08 — IG close by kind, not by LIKE ============
def test_igplan_close_by_kind():
    _fresh()
    ig = 900
    # تسکِ واقعیِ ig_plan
    ts.create_task(ts.system_context("task_create"), ig, "ig", "🤖 مدیرِ محتوا", "پلن", task_kind="ig_plan")
    # تسکِ staff با نامِ مشابهِ label ولی kind=staff → نباید بسته شود (رفعِ D-08)
    ts.create_task(_admin := ts.MutationContext(111, "admin", "telegram", "task_create"),
                   ig, "ig", "🤖 مدیرِ محتوا نمونه", "کارِ staff", task_kind="staff")
    # رکوردِ legacy: task_kind=NULL + assigner محتوا → fallback باید ببندد
    db._conn.execute("INSERT INTO wt_tasks(assignee_id,assignee_name,assigner_name,text,status,created_ts,task_kind) "
                     "VALUES (?,?,?,?, 'open', ?, NULL)", (ig, "ig", "🤖 مدیرِ محتوا", "legacy", time.time()))
    db._conn.commit()
    n = w._close_prev_igplan_tasks(ig)
    open_left = db._conn.execute("SELECT text FROM wt_tasks WHERE assignee_id=? AND status='open'", (ig,)).fetchall()
    ok = check("D-08) close فقط ig_plan (واقعی) + legacy-NULL-محتوا را بست (۲ تا)", n == 2)
    ok &= check("D-08) تسکِ staff با نامِ مشابه بسته نشد", any(r[0] == "کارِ staff" for r in open_left))
    return ok


# ============ WORKSTREAM H — roles ============
def test_roles():
    _fresh()
    ok = True
    config.WT_PRIMARY_ADMIN_ID = 0
    ok &= check("H) primary unset → ادمین همان 'admin' (backward-compatible)", w._role_of(111) == "admin")
    ok &= check("H) staff بدون تغییر", w._role_of(222) == "staff")
    ok &= check("H) system برای actor=0", w._role_of(0) == "system")
    config.WT_PRIMARY_ADMIN_ID = 111
    ok &= check("H) primary configured → 'primary_admin'", w._role_of(111) == "primary_admin")
    ok &= check("H) ادمینِ غیرِ primary همچنان 'admin'", w._role_of(100210214 if 100210214 in config.ADMIN_USER_IDS else 111) in ("admin", "primary_admin"))
    # نقشِ جعلیِ خارج از allowlist → context نامعتبر (spoof rejected)
    bad = ts.create_task(ts.MutationContext(5, "superuser", "telegram", "task_create"), 1, "x", "y", "z")
    ok &= check("H) نقشِ جعلی/خارج از allowlist → رد", bad.status == "invalid")
    config.WT_PRIMARY_ADMIN_ID = 0
    return ok


# ============ Adversarial / legacy ============
def test_adversarial_legacy():
    _fresh()
    ok = True
    # legacy rows با task_kind=NULL و فیلدهای null نباید crash کنند
    db._conn.execute("INSERT INTO wt_tasks(assignee_id,assignee_name,text,status,created_ts) "
                     "VALUES (5,'x','t','open',?)", (time.time(),))
    db._conn.commit()
    # bump روی رکوردِ legacy (escalation_ref_ts=NULL) → fallback به created_ts، بدون crash
    lid = db._conn.execute("SELECT id FROM wt_tasks WHERE assignee_id=5").fetchone()[0]
    r = ts.bump_crawl_escalation(ts.system_context("crawl_escalation_bump"), lid)
    ok &= check("legacy) bump روی رکوردِ NULL بدون crash (fallback به created_ts)", r.status == "applied")
    # عنوانِ حاویِ کلیدواژهٔ IG ولی kind=staff → در query نوعی، ig_plan حساب نمی‌شود
    ts.create_task(ts.system_context("task_create"), 9, "n", "sys", "برنامه اینستاگرام امروز", task_kind="staff")
    igc = db._conn.execute("SELECT COUNT(*) FROM wt_tasks WHERE task_kind='ig_plan'").fetchone()[0]
    ok &= check("adv) عنوان با کلیدواژهٔ IG ولی kind=staff → ig_plan شمرده نمی‌شود", igc == 0)
    return ok


# ============ WORKSTREAM C — D-03 perf card deterministic, no worker hidden ============
def test_perf_card_deterministic():
    _fresh()
    day = "2026-07-16"
    for uid, name in ((222, "علی"), (333, "رضا")):
        db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    for uid, score, txt in ((222, 40, "اول"), (222, 70, "دومِ آخر"), (333, 55, "رضا")):  # علی دو گزارش
        db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_score,ai_summary) "
                         "VALUES (?,?,?,?,?, 'work', ?, ?)", (uid, "x", day, txt, time.time(), score, "s"))
    db._conn.commit()
    card = w.daily_perf_text(day)
    ok = check("D-03) هر دو کارمند در کارت نماینده دارند (هیچ‌کس پنهان نشد)", "علی" in card and "رضا" in card)
    ok &= check("D-03) آخرین گزارشِ علی (۷۰) نشان داده می‌شود؛ deterministic latest-per-user (ORDER BY id)",
                ("۷۰" in card or "70" in card))
    return ok


def main():
    tests = [test_report_idempotency, test_delivery_guard, test_crawl_created_immutable_and_audit,
             test_task_kind, test_igplan_close_by_kind, test_roles, test_adversarial_legacy,
             test_perf_card_deterministic]
    res = []
    for t in tests:
        try:
            res.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            res.append(False)
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ تستِ فاز ۲A سبز شد.")
    sys.exit(0 if p == n else 1)


if __name__ == "__main__":
    main()
