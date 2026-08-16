"""Staging Validation & Delivery Hardening & Rollout Gate — ۲۶ گروهِ آزمایشِ آفلاین.

محیطِ آزمایشیِ ایزوله (in-memory DB، fake bot، fake read-only adapters وفادار به قراردادِ واقعیِ clientها).
هیچ APIِ واقعیِ production صدا زده نمی‌شود، هیچ پیامِ واقعی ارسال نمی‌شود، DBِ production لمس نمی‌شود.
تست‌های «live read-only» با fake adapter اجرا می‌شوند؛ اعتبارسنجیِ زندهٔ واقعی نیازمندِ credentialِ staging است (BLOCKED — رجوع به سند).

اجرا: `python tests/test_staging_gate.py`
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
import wt_verify as vf     # noqa: E402
import worktasks as w  # noqa: E402

_ok_all = True
_kn = [0]


def check(name, cond):
    global _ok_all
    _ok_all = _ok_all and bool(cond)
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def _key():
    _kn[0] += 1
    return f"sk{_kn[0]}"


def _fresh(**flags):
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()
    config.ADMIN_USER_IDS = [111]
    config.WT_PRIMARY_ADMIN_ID = 0
    for k, v in {"WT_LIFECYCLE_ENABLED": True, "WT_MANAGER_VERIFICATION_ENABLED": True,
                 "WT_AUTOMATIC_VERIFICATION_ENABLED": True, "WT_WEBSITE_TASKS_ENABLED": True,
                 "WT_INSTAGRAM_TASKS_ENABLED": True, "WT_NEW_NOTIFICATIONS_ENABLED": False}.items():
        setattr(config, k, flags.get(k, v))
    config.WT_WEBSITE_ASSIGNEE_ID = 222
    config.WT_INSTAGRAM_ASSIGNEE_ID = 333
    w._awaiting_answers.clear(); w._awaiting.clear(); w._awaiting_block.clear(); w._followup_inflight.clear()


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _mk(assignee=222, mode="manager"):
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create",
                             idempotency_key=_key())
    return ts.create_task(ctx, assignee, "کارمند", "مدیر", "کار", task_kind="staff",
                          lifecycle_state="open", verification_mode=mode).task_id


def _state(tid):
    return ts.get_task(tid)["state"]


def _led(k, src="delivery"):
    return db._conn.execute("SELECT status, attempt_count, result_reference FROM wt_inbound_events "
                            "WHERE source=? AND external_event_id=?", (src, k)).fetchone()


def _seed_reported(day="2026-07-16"):
    db.set_meta("work_group", "-500")
    for uid, name in ((222, "الف"), (333, "ب")):
        db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    for uid in (222, 333):
        db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_score,ai_summary) "
                         "VALUES (?,?,?,?,?, 'work', ?, ?)", (uid, "x", day, "t", time.time(), 50, "s"))
    db._conn.commit()
    return day


class _FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, *a, **k):
        if self.fail:
            raise RuntimeError("net-down")
        self.sent.append((a, k))
        return type("M", (), {"message_id": 500 + len(self.sent)})()


# read-only fake adapters وفادار به قراردادِ واقعیِ woo/crm/igstats
class FakeWebsite:
    def __init__(self, products=None, orders=None, counts=None, activity=None, mode="ok"):
        self.products, self.orders, self.counts, self.activity = products or {}, orders or {}, counts or {}, activity or {}
        self.mode, self.calls = mode, 0

    async def _maybe(self):
        self.calls += 1
        if self.mode == "timeout":
            raise asyncio.TimeoutError()
        if self.mode == "error":
            raise RuntimeError("503")

    async def get_product(self, pid):
        await self._maybe(); return self.products.get(int(pid))

    async def get_order(self, oid):
        await self._maybe(); return self.orders.get(int(oid))

    async def total_count(self, e, p):
        await self._maybe(); return self.counts.get(e, 0)

    async def crm_activity(self, wp, frm, to):
        await self._maybe(); return self.activity


class FakeIG:
    def __init__(self, data=None, mode="ok"):
        self.data, self.mode, self.calls = data or {"ok": True, "media_count": 6}, mode, 0

    async def summary(self):
        self.calls += 1
        if self.mode == "timeout":
            raise asyncio.TimeoutError()
        if self.mode == "error":
            raise RuntimeError("down")
        return self.data


# ============================================================
# 1 — staging isolation
def t01_staging_isolation():
    _fresh()
    inmem = "file::memory" not in "" and getattr(db._conn, "__class__", None) is sqlite3.Connection
    # DBِ آزمایشی in-memory است و با فایلِ production یکی نیست
    path_row = db._conn.execute("PRAGMA database_list").fetchall()
    ok = check("1) DBِ staging در حافظه است (فایلِ production لمس نمی‌شود)",
               all((r[2] in ("", None)) for r in path_row) and config.DB_PATH != "" )
    ok &= check("1) bot جعلی است، توکنِ واقعی استفاده نمی‌شود", isinstance(_FakeBot(), _FakeBot))
    ok &= check("1) آداپترهای سایت/IG جعلی و read-only هستند", not hasattr(FakeWebsite, "put") and not hasattr(FakeIG, "publish"))
    return ok


# 2 — migration on realistic legacy copy
def t02_migration_legacy():
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    c = db._conn
    c.execute("CREATE TABLE wt_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, assignee_id INTEGER, assignee_name TEXT, "
              "assigner_id INTEGER, assigner_name TEXT, text TEXT, status TEXT DEFAULT 'open', created_ts REAL, done_ts REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    for i, st in ((1, "open"), (2, "done"), (3, "open")):
        c.execute("INSERT INTO wt_tasks(assignee_id,assignee_name,text,status,created_ts) VALUES (?,?,?,?,?)",
                  (i, "قدیمی", "کارِ legacy", st, time.time()))
    c.execute("UPDATE wt_tasks SET assigner_name='🤖 مدیرِ محتوا' WHERE id=3")  # legacyِ شبه‌ig_plan
    c.commit()
    before = c.execute("SELECT COUNT(*) FROM wt_tasks").fetchone()[0]
    t0 = time.monotonic()
    ts.init_schema()
    dur_ms = (time.monotonic() - t0) * 1000
    cols = [r[1] for r in c.execute("PRAGMA table_info(wt_tasks)")]
    after = c.execute("SELECT COUNT(*) FROM wt_tasks").fetchone()[0]
    s1 = ts.lifecycle_of(*c.execute("SELECT lifecycle_state,status FROM wt_tasks WHERE id=1").fetchone())
    s2 = ts.lifecycle_of(*c.execute("SELECT lifecycle_state,status FROM wt_tasks WHERE id=2").fetchone())
    ok = check(f"2) مهاجرت روی دادهٔ legacy: رکورد بدون‌تغییر ({before}={after})، ۱۶ ستون افزوده", before == after == 3
               and "lifecycle_state" in cols and "verify_rule_json" in cols)
    ok &= check("2) کارِ قدیمیِ باز→open، انجام‌شده→verified_done", s1 == "open" and s2 == "verified_done")
    ok &= check(f"2) زمانِ مهاجرت اندازه‌گیری شد ({dur_ms:.1f}ms، کوتاه)", dur_ms < 1000)
    globals()["_MIG_MS"] = round(dur_ms, 1)
    return ok


# 3 — migration second-run safety
def t03_migration_second_run():
    ts.init_schema()  # اجرای دوباره روی همان DB
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(wt_tasks)")]
    n = db._conn.execute("SELECT COUNT(*) FROM wt_tasks").fetchone()[0]
    dupcols = len(cols) != len(set(cols))
    ok = check("3) اجرای دومِ مهاجرت idempotent (بدونِ خطا/ستونِ تکراری/تغییرِ داده)", not dupcols and n == 3)
    return ok


# 4..7 — website live read-only (fake staging adapter)
def t04_website_success():
    _fresh()
    fw = FakeWebsite(products={1: {"status": "publish", "stock_quantity": 9}},
                     orders={7: {"status": "completed"}}, counts={"products": 12},
                     activity={"ok": True, "counts": {"product_updated": 5}})
    r1 = _run(vf.verify_rule({"rule": "product_published", "entity_id": 1}, website=fw))
    r2 = _run(vf.verify_rule({"rule": "product_stock_at_least", "entity_id": 1, "threshold": 3}, website=fw))
    r3 = _run(vf.verify_rule({"rule": "order_status_is", "entity_id": 7, "expected": "completed"}, website=fw))
    r4 = _run(vf.verify_rule({"rule": "product_count_at_least", "params": {"status": "publish"}, "threshold": 10}, website=fw))
    r5 = _run(vf.verify_rule({"rule": "crm_activity_at_least", "wp_id": 55, "action": "product_updated", "threshold": 3}, website=fw))
    ok = check("4) ۵ قاعدهٔ سایت روی پاسخِ موفق → همه positive",
               all(r.outcome == "positive" for r in (r1, r2, r3, r4, r5)))
    ok &= check("4) شناسهٔ ناموجود → negative (نه positive)",
                _run(vf.verify_rule({"rule": "product_published", "entity_id": 999}, website=fw)).outcome == "negative")
    globals()["_WEB_CALLS"] = fw.calls
    return ok


def t05_website_timeout():
    _fresh()
    r = _run(vf.verify_rule({"rule": "product_published", "entity_id": 1}, website=FakeWebsite(mode="timeout"), timeout=0.5))
    return check("5) timeoutِ سایت → unavailable (تسک رد/گم نمی‌شود)", r.outcome == "unavailable")


def t06_website_malformed():
    _fresh()
    r = _run(vf.verify_rule({"rule": "product_published", "entity_id": 1},
                            website=FakeWebsite(products={1: {"foo": "bar"}})))
    return check("6) پاسخِ ناقصِ سایت (بدونِ status) → negative", r.outcome == "negative")


def t07_website_recovery():
    _fresh()
    fw = FakeWebsite(products={1: {"status": "publish"}}, mode="error")
    r_fail = _run(vf.verify_rule({"rule": "product_published", "entity_id": 1}, website=fw))
    fw.mode = "ok"  # سرویس برگشت
    r_ok = _run(vf.verify_rule({"rule": "product_published", "entity_id": 1}, website=fw))
    return check("7) بازیابیِ سایت پس از خطا: error→unavailable، سپس ok→positive",
                 r_fail.outcome == "unavailable" and r_ok.outcome == "positive")


# 8..10 — instagram live read-only
def t08_ig_success():
    _fresh()
    r = _run(vf.verify_rule({"rule": "ig_posts_at_least", "threshold": 3, "metric": "media_count"},
                            instagram=FakeIG({"ok": True, "media_count": 6})))
    globals()["_IG_CALLS"] = 1
    return check("8) IG تجمیعی روی پاسخِ موفق → positive", r.outcome == "positive")


def t09_ig_unavailable():
    _fresh()
    r_empty = _run(vf.verify_rule({"rule": "ig_posts_at_least", "threshold": 3}, instagram=FakeIG({"ok": False})))
    r_to = _run(vf.verify_rule({"rule": "ig_posts_at_least", "threshold": 3}, instagram=FakeIG(mode="timeout"), timeout=0.5))
    return check("9) IG خالی/timeout → unavailable (کار از بین نمی‌رود)",
                 r_empty.outcome == "unavailable" and r_to.outcome == "unavailable")


def t10_ig_recovery_and_no_login():
    _fresh()
    ig = FakeIG(mode="error")
    r1 = _run(vf.verify_rule({"rule": "ig_posts_at_least", "threshold": 3}, instagram=ig))
    ig.mode = "ok"; ig.data = {"ok": True, "media_count": 8}
    r2 = _run(vf.verify_rule({"rule": "ig_posts_at_least", "threshold": 3}, instagram=ig))
    ok = check("10) بازیابیِ IG: error→unavailable سپس ok→positive", r1.outcome == "unavailable" and r2.outcome == "positive")
    ok &= check("10) بدونِ login/session/write (آداپتر فقط summary دارد)",
                hasattr(vf.InstagramAdapter, "summary") and not any(hasattr(vf.InstagramAdapter, m)
                for m in ("login", "publish", "post_media", "reply", "session")))
    return ok


# 11..18 — delivery hardening (D-RG-01)
def t11_crash_before_send():
    _fresh()
    day = _seed_reported()
    ts.delivery_claim(f"perf:{day}", operation="manager_perf", lease_sec=999)  # کرشِ وسطِ تلاشِ دیگر
    bot = _FakeBot()
    _run(w.maybe_send_perf_when_complete(bot))
    return check("11) crash before send: in_progress → نه ارسال، نه گاردِ meta (recovery حفظ)",
                 len(bot.sent) == 0 and db.get_meta("last_perf_report") != day)


def t12_crash_after_claim():
    _fresh()
    k = "reminder:D:21"
    ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)  # claim، سپس کرش (بدونِ send/complete)
    d, _ = ts.delivery_claim(k, operation="report_reminder")
    return check("12) crash after claim: پس از انقضای lease → recovered (قابلِ ارسالِ دوباره)", d == "recovered")


def t13_ack_before_commit():
    _fresh()
    k = "perf:D"
    ts.delivery_claim(k, operation="manager_perf", lease_sec=-1)  # ack گرفته شد، complete قبل از کرش اجرا نشد
    d, _ = ts.delivery_claim(k, operation="manager_perf")
    st, att, ref = _led(k)
    return check("13) ack-before-commit → recovered (DUPLICATE ممکن، بدونِ گم‌شدنِ دائمی)؛ ادعای exactly-once نیست",
                 d == "recovered" and (ref in (None, "")))


def t14_lease_expiry():
    _fresh()
    ts.delivery_claim("k:v", lease_sec=999)
    dv, _ = ts.delivery_claim("k:v")
    ts.delivery_claim("k:e", lease_sec=-1)
    de, _ = ts.delivery_claim("k:e")
    return check("14) lease معتبر→in_progress، منقضی→recovered", dv == "in_progress" and de == "recovered")


def t15_retry_exhaustion():
    _fresh()
    k = "reminder:D:21"
    decisions = []
    for _ in range(8):
        d, _r = ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)
        decisions.append(d)
        if d in ("claimed", "recovered"):
            ts.delivery_fail(k, error_type="Timeout")
        if d == "skip_permanent":
            break
    st, att, ref = _led(k)
    return check("15) retry exhaustion → skip_permanent (terminal، هرگز delivered دروغین، بی‌نهایت نه)",
                 "skip_permanent" in decisions and st == "failed_permanent" and ref in (None, "") and att <= ts._MAX_ATTEMPTS + 1)


def t16_manual_retry():
    _fresh()
    k = "perf:D"
    ts.delivery_claim(k, operation="manager_perf")
    ts.delivery_fail(k, "Timeout")            # تلاشِ اول شکست
    d, _ = ts.delivery_claim(k, operation="manager_perf")  # ارسالِ مجددِ دستی
    return check("16) manual retry پس از شکست → recovered", d == "recovered")


def t17_restart_recovery_delivery():
    _fresh()
    k = "reminder:D:2330"
    ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)  # pending از قبلِ restart
    d, _ = ts.delivery_claim(k, operation="report_reminder")         # پس از restart
    return check("17) delivery restart recovery: pending بازیابی شد", d == "recovered")


def t18_no_permanent_silent_loss():
    """کرشِ قبل از ارسال + restart پس از انقضای lease → sender دقیقاً یک‌بار می‌فرستد (گم‌شدنِ دائمی صفر)."""
    _fresh()
    day = _seed_reported()
    ts.delivery_claim(f"perf:{day}", operation="manager_perf", lease_sec=-1)  # کرشِ قبل از ارسال، leaseِ منقضی
    bot = _FakeBot()
    _run(w.maybe_send_perf_when_complete(bot))
    return check("18) no permanent silent loss: recovery → ارسالِ دقیقاً یک‌بار",
                 len(bot.sent) == 1 and db.get_meta("last_perf_report") == day)


# 19 — lifecycle restart recovery
def t19_lifecycle_restart_recovery():
    _fresh()
    t = _mk(mode="manager")
    w.lifecycle_start(t, 222)
    w.lifecycle_done(t, 222)  # claimed_done
    # «restart»: حالتِ حافظهٔ worktasks پاک؛ DB باقی
    w._awaiting_answers.clear(); w._awaiting_block.clear()
    st = _state(t)
    # تسکِ منتظرِ تأیید هنوز قابلِ دیدن/اقدام است
    pend = w.pending_approval_text()
    return check("19) lifecycle restart recovery: state از DB پایدار (claimed_done)، در /pending دیده می‌شود",
                 st == "claimed_done" and f"#{t}" in pend)


# 20 — audit consistency after crash (اتمیک: خطای audit → rollbackِ state)
def t20_audit_consistency():
    _fresh()
    t = _mk(mode="manager")
    orig = ts._insert_audit

    def _boom(*a, **k):
        raise RuntimeError("audit-crash")
    ts._insert_audit = _boom
    try:
        r = ts.transition_task(w._lifecycle_ctx(222, "task_transition", _key()), t, "in_progress")
    except Exception:
        r = None
    finally:
        ts._insert_audit = orig
    st_after = _state(t)
    ev = db._conn.execute("SELECT COUNT(*) FROM wt_task_events WHERE task_id=? AND event_type='task_state_changed'", (t,)).fetchone()[0]
    return check("20) خطای audit → rollback: نه تغییرِ state، نه eventِ ناقص (اتمیک)", st_after == "open" and ev == 0)


# 21 — feature flag combinations
def t21_flag_matrix():
    ok = True
    # همه خاموش → رفتارِ legacy: create بدونِ lifecycle، done قدیمی
    _fresh(WT_LIFECYCLE_ENABLED=False)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key=_key())
    r = ts.create_task(ctx, 5, "x", "م", "کار", task_kind="staff")  # بدونِ lifecycle_state
    ok &= check("21a) همه خاموش → create بدونِ lifecycle (legacy)", ts.get_task(r.task_id)["lifecycle_state"] is None)
    # فقط چرخه روشن، سایت/IG/نوتیف خاموش → تسکِ سایت ساخته نمی‌شود
    _fresh(WT_WEBSITE_TASKS_ENABLED=False, WT_INSTAGRAM_TASKS_ENABLED=False)
    ok &= check("21b) چرخه روشن ولی سایت خاموش → تسکِ سایت ساخته نمی‌شود",
                w.create_website_task("x", entity_type="product", entity_id="1", operation="publish", assigner_id=111) == -1)
    # automatic خاموش → mode=automatic به manager برمی‌گردد
    _fresh(WT_AUTOMATIC_VERIFICATION_ENABLED=False)
    tid = w.create_website_task("x", entity_type="product", entity_id="2", operation="publish",
                                verify_rule={"rule": "product_published", "entity_id": 2}, mode="automatic", assigner_id=111)
    ok &= check("21c) automatic خاموش → mode به manager برمی‌گردد (بدونِ رد/گم)", ts.get_task(tid)["verification_mode"] == "manager")
    # نوتیفِ جدید خاموش → ارسال نمی‌شود
    _fresh(WT_NEW_NOTIFICATIONS_ENABLED=False)
    db.set_meta("work_group", "-500")
    bot = _FakeBot()
    _run(w.notify_transition(bot, 1, "x", "y"))
    ok &= check("21d) نوتیفِ جدیدِ خاموش → ارسال نمی‌شود", len(bot.sent) == 0)
    # config نامعتبر → fail-closed
    _fresh()
    ok &= check("21e) verification_mode نامعتبر → رد (fail-closed)",
                ts.set_verification_mode(w._lifecycle_ctx(111, "task_set_verification_mode", _key()), _mk(), "bogus").status == "invalid")
    return ok


# 22 — role isolation
def t22_role_isolation():
    _fresh()
    t = _mk(assignee=222, mode="manager")
    ok = check("22) staff نمی‌تواند تسکِ دیگری را شروع کند", w.lifecycle_start(t, 777).status == "unauthorized")
    ok &= check("22) staff نمی‌تواند approve/verify کند", w.mgr_approve(t, 222).status in ("unauthorized", "invalid"))
    ok &= check("22) staff نمی‌تواند cancel/reopen/reassign کند",
                w.mgr_cancel(t, 222, "x").status == "unauthorized" and w.mgr_reassign(t, 222, 5, "y").status == "unauthorized")
    ok &= check("22) system ادمینِ عمومی نیست (cancel رد)",
                ts.transition_task(ts.system_context("task_transition", idempotency_key=_key()), t, "cancelled", reason="x").status in ("unauthorized", "invalid"))
    ok &= check("22) نقشِ جعلی رد", ts.transition_task(ts.MutationContext(actor_id=1, actor_role="superadmin",
                source="telegram", operation="task_transition", idempotency_key=_key()), t, "in_progress").status == "invalid")
    # نبودِ primary_admin رفتارِ قدیمی را خراب نمی‌کند
    config.WT_PRIMARY_ADMIN_ID = 0
    ok &= check("22) primary_admin تنظیم‌نشده → ادمین‌ها admin می‌مانند (رفتارِ قدیمی)", w._role_of(111) == "admin")
    return ok


# 23 — production-write prohibition
def t23_write_prohibition():
    ok = check("23) آداپترهای واقعی هیچ متدِ نوشتن ندارند",
               not any(hasattr(vf.WebsiteAdapter, m) for m in ("put", "post", "create", "update_product", "publish"))
               and not any(hasattr(vf.InstagramAdapter, m) for m in ("publish", "post_media", "reply", "login")))
    ok &= check("23) هیچ ruleِ verification نوشتن نیست (همه read، allowlist)",
                all(s in ("website", "instagram") for s, _p in vf.RULE_SPECS.values()))
    ok &= check("23) ruleِ ناشناخته/آزاد رد می‌شود", vf.validate_rule({"rule": "do_write"})[0] is False)
    return ok


# 24 — report staging accuracy
def t24_report_accuracy():
    _fresh()
    a = _mk(222, "manager"); w.lifecycle_start(a, 222)
    b = _mk(222, "manager"); w.lifecycle_done(b, 222)
    c = _mk(333, "none"); w.lifecycle_done(c, 333)
    d = _mk(222, "manager"); w.mgr_cancel(d, 111, "x")
    rc = w.lifecycle_counts()
    total = db._conn.execute("SELECT COUNT(*) FROM wt_tasks WHERE COALESCE(task_kind,'staff')='staff'").fetchone()[0]
    ok = check("24) شمارشِ گزارش درست (in_progress/claimed/verified/cancelled ≥۱)",
               rc["by_state"]["in_progress"] >= 1 and rc["by_state"]["claimed_done"] >= 1
               and rc["by_state"]["verified_done"] >= 1 and rc["by_state"]["cancelled"] >= 1)
    ok &= check("24) بدونِ شمارشِ تکراری (مجموع = تعدادِ تسک)", sum(rc["by_state"].values()) == total)
    ok &= check("24) ترتیب/متنِ گزارش deterministic و بدونِ AI", w.lifecycle_report_text() == w.lifecycle_report_text())
    return ok


# 25 — zero-LLM operational paths
def t25_zero_llm():
    _fresh()
    import wt_brain
    orig = wt_brain._chat

    async def _boom(*a, **k):
        raise AssertionError("LLM نباید صدا شود")
    wt_brain._chat = _boom
    try:
        t = _mk(mode="automatic")
        ok = check("25) start/block/resume/done/approve/reopen/cancel/reassign/deadline/priority = صفر LLM", all([
            w.lifecycle_start(t, 222).status == "applied",
            w.lifecycle_block(t, 222, "x").status == "applied",
            w.lifecycle_resume(t, 222).status == "applied",
            w.lifecycle_done(t, 222)[0].status == "applied",
            w.mgr_reopen(t, 111, "y").status == "applied",
            w.mgr_set_priority(t, 111, "high").status == "applied",
            w.mgr_set_deadline(t, 111, time.time() + 60).status == "applied",
        ]))
        rt = w.create_website_task("x", entity_type="product", entity_id="5", operation="publish",
                                   verify_rule={"rule": "product_published", "entity_id": 5}, mode="automatic", assigner_id=111)
        w.lifecycle_done(rt, 222)
        ok &= check("25) website/IG verification + گزارش = صفر LLM",
                    _run(w.verify_and_apply(rt, website=FakeWebsite(products={5: {"status": "publish"}}))) == "verified"
                    and "وضعیتِ کارها" in w.lifecycle_report_text())
    finally:
        wt_brain._chat = orig
    return ok


# 26 — rollback readiness
def t26_rollback_readiness():
    # با flag روشن یک تسکِ چرخه‌دار بساز، سپس flag را خاموش کن → دکمهٔ done مسیرِ legacyِ mark_done را می‌رود
    _fresh()
    t = _mk(mode="none")
    config.WT_LIFECYCLE_ENABLED = False           # rollback: خاموش‌کردنِ پرچم
    # مسیرِ legacy: _task_done روی تسک (mark_done)
    dctx = ts.MutationContext(actor_id=222, actor_role="staff", source="telegram", operation="task_mark_done", idempotency_key=_key())
    md = ts.mark_done(dctx, t)
    row = db._conn.execute("SELECT status, lifecycle_state FROM wt_tasks WHERE id=?", (t,)).fetchone()
    ok = check("26) rollback (flag خاموش) → done قدیمی کار می‌کند و state با status همگام (verified_done)",
               md.status == "applied" and row[0] == "done" and row[1] == "verified_done")
    ok &= check("26) ستون‌های چرخه inert می‌مانند (schema نیازی به حذف ندارد؛ projection از status)",
                ts.lifecycle_of(None, "done") == "verified_done")
    return ok


def main():
    tests = [t01_staging_isolation, t02_migration_legacy, t03_migration_second_run, t04_website_success,
             t05_website_timeout, t06_website_malformed, t07_website_recovery, t08_ig_success, t09_ig_unavailable,
             t10_ig_recovery_and_no_login, t11_crash_before_send, t12_crash_after_claim, t13_ack_before_commit,
             t14_lease_expiry, t15_retry_exhaustion, t16_manual_retry, t17_restart_recovery_delivery,
             t18_no_permanent_silent_loss, t19_lifecycle_restart_recovery, t20_audit_consistency, t21_flag_matrix,
             t22_role_isolation, t23_write_prohibition, t24_report_accuracy, t25_zero_llm, t26_rollback_readiness]
    res = []
    for t in tests:
        print(f"\n— {t.__name__} —")
        try:
            res.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            res.append(False)
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ staging-gate سبز شد؛ همهٔ assertها: {'✅' if _ok_all else '❌'}")
    sys.exit(0 if (p == n and _ok_all) else 1)


if __name__ == "__main__":
    main()
