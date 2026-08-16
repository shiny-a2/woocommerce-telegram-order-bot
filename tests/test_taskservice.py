"""تستِ فاز ۱ (Operational Integrity): idempotency ورودی/عملیات، mutationِ اتمیک + audit، append-only، authz، recovery.

کاملاً آفلاین: دیتابیسِ in-memory، بدونِ هیچ API/شبکه. اجرا: `python tests/test_taskservice.py`.
"""
import os
import sqlite3
import sys
import threading

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import db              # noqa: E402
import taskservice as ts   # noqa: E402
import worktasks as w  # noqa: E402


def check(name, cond):
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def _fresh_db():
    """دیتابیسِ in-memoryِ نو + همهٔ جدول‌های wt + audit/inbound (بدونِ لمسِ فایلِ production)."""
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()   # wt_tasks/reports/staff/directives + taskservice.init_schema()
    config.ADMIN_USER_IDS = [111]   # ادمینِ تستی


def _admin_ctx(op, idem="", actor=111):
    return ts.MutationContext(actor_id=actor, actor_role="admin", source="telegram", operation=op,
                              source_event_id="u1", idempotency_key=idem)


def _staff_ctx(op, actor, idem=""):
    return ts.MutationContext(actor_id=actor, actor_role="staff", source="telegram", operation=op,
                              source_event_id="u1", idempotency_key=idem)


def _events(task_id=None):
    q = "SELECT event_type, idempotency_key, actor_id, actor_role FROM wt_task_events"
    args = ()
    if task_id is not None:
        q += " WHERE task_id=?"; args = (task_id,)
    return db._conn.execute(q + " ORDER BY id", args).fetchall()


def _count(tbl, where="", args=()):
    return db._conn.execute(f"SELECT COUNT(*) FROM {tbl}" + (f" WHERE {where}" if where else ""), args).fetchone()[0]


# ---------------- idempotency ----------------
def test_idem_create_and_inbound():
    _fresh_db()
    ok = True
    # 1 & 10: retryِ create با همان idempotency-key → همان task ID، بدونِ تسکِ دوم
    c1 = ts.create_task(_admin_ctx("task_create", "telegram:5:task_create:0"), 222, "علی", "مدیر", "کار A")
    c2 = ts.create_task(_admin_ctx("task_create", "telegram:5:task_create:0"), 222, "علی", "مدیر", "کار A")
    ok &= check("۱/۱۰) retry create همان task ID و یک تسک", c1.status == "applied" and c2.status == "duplicate"
                and c1.task_id == c2.task_id and _count("wt_tasks") == 1)
    # 11: دقیقاً یک task_created event
    ok &= check("۱۱) create دقیقاً یک task_created event", _count("wt_task_events", "event_type='task_created'") == 1)
    # 3: دو update متفاوت با متنِ یکسان → دو تسکِ مستقل
    ts.create_task(_admin_ctx("task_create", "telegram:6:task_create:0"), 222, "علی", "مدیر", "کار A")
    ok &= check("۳) دو updateِ متفاوتِ هم‌متن → دو تسکِ مستقل", _count("wt_tasks") == 2)
    # inbound claim: 1st=claimed, 2nd=duplicate/in_progress
    d1, _ = ts.claim_inbound("telegram", 900, operation="task_create", actor_id=111)
    ts.complete_inbound("telegram", 900, "ok")
    d2, _ = ts.claim_inbound("telegram", 900, operation="task_create", actor_id=111)
    ok &= check("۲) همان updateِ موفق دوباره → duplicate (بدونِ پردازش)", d1 == "claimed" and d2 == "duplicate")
    return ok


def test_idem_concurrent_claim():
    _fresh_db()
    results, barrier = [], threading.Barrier(2)

    def worker():
        barrier.wait()
        results.append(ts.claim_inbound("telegram", 42, operation="x", actor_id=111)[0])

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    claimed = sum(1 for r in results if r in ("claimed", "recovered"))
    return check("۴) دو thread یک update را claim می‌کنند؛ فقط یکی اجرا (claimed) و دیگری نه",
                 claimed == 1 and "in_progress" in results)


def test_idem_lease_recovery():
    _fresh_db()
    ok = True
    # lease معتبر → دوباره اجرا نشود
    ts.claim_inbound("telegram", 7, operation="x", actor_id=111, lease_sec=999)
    d_valid, _ = ts.claim_inbound("telegram", 7, operation="x", actor_id=111)
    ok &= check("۶) lease معتبرِ processing دوباره اجرا نمی‌شود", d_valid == "in_progress")
    # lease منقضی → recovery
    ts.claim_inbound("telegram", 8, operation="x", actor_id=111, lease_sec=-1)   # فوراً منقضی
    d_exp, _ = ts.claim_inbound("telegram", 8, operation="x", actor_id=111)
    ok &= check("۵) processing lease منقضی‌شده recovery می‌شود", d_exp == "recovered")
    return ok


def test_idem_retry_caps():
    _fresh_db()
    ok = True
    # failed_permanent → دیگر پردازش نشود (بی‌نهایت retry نه)
    ts.claim_inbound("telegram", 9, operation="x", actor_id=111)
    ts.fail_inbound("telegram", 9, "ValueError", permanent=True)
    d_perm, _ = ts.claim_inbound("telegram", 9, operation="x", actor_id=111)
    ok &= check("۷) failed_permanent بی‌نهایت retry نمی‌شود", d_perm == "skip_permanent")
    # عبور از سقفِ attempt → permanent
    for _ in range(6):
        ts.claim_inbound("telegram", 10, operation="x", actor_id=111, lease_sec=-1, max_attempts=3)
    d_cap, _ = ts.claim_inbound("telegram", 10, operation="x", actor_id=111, lease_sec=-1, max_attempts=3)
    ok &= check("۸) عبور از سقفِ retry → failed_permanent", d_cap == "skip_permanent")
    return ok


def test_idem_key_conflict():
    _fresh_db()
    # 9: همان idempotency key برای operation متفاوت → conflict (دومی duplicateِ همان event، mutationِ جدید نمی‌سازد)
    a = ts.create_task(_admin_ctx("task_create", "telegram:1:x"), 222, "علی", "مدیر", "کار")
    b = ts.mark_done(_admin_ctx("task_mark_done", "telegram:1:x"), a.task_id)
    return check("۹) کلیدِ idempotency یکسان با operation متفاوت → mutationِ جدید نمی‌سازد (duplicate)",
                 b.status == "duplicate" and _count("wt_task_events", "event_type='task_marked_done'") == 0)


def test_mark_done_idempotent():
    _fresh_db()
    ok = True
    a = ts.create_task(_admin_ctx("task_create", "k1"), 222, "علی", "مدیر", "کار")
    d1 = ts.mark_done(_admin_ctx("task_mark_done", "telegram:2:done"), a.task_id)
    d2 = ts.mark_done(_admin_ctx("task_mark_done", "telegram:2:done"), a.task_id)   # retryِ همان key
    ok &= check("۱۲) mark done یک event", d1.status == "applied"
                and _count("wt_task_events", "event_type='task_marked_done'") == 1)
    ok &= check("۱۳) retry mark done event دوم نمی‌سازد", d2.status == "duplicate"
                and _count("wt_task_events", "event_type='task_marked_done'") == 1)
    return ok


# ---------------- audit ----------------
def test_audit_atomic_and_rollback():
    _fresh_db()
    ok = True
    # 15: شکستِ audit → rollbackِ mutation (تسک ساخته نشود)
    orig = ts._insert_audit
    ts._insert_audit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit fail"))
    raised = False
    try:
        ts.create_task(_admin_ctx("task_create", "kx"), 222, "علی", "مدیر", "کار")
    except RuntimeError:
        raised = True
    ts._insert_audit = orig
    ok &= check("۱۴/۱۵) audit fail → rollback؛ نه تسک نه event", raised and _count("wt_tasks") == 0
                and _count("wt_task_events") == 0)
    return ok


def test_audit_append_only():
    _fresh_db()
    a = ts.create_task(_admin_ctx("task_create", "k"), 222, "علی", "مدیر", "کار")
    ok = True
    up_blocked = del_blocked = False
    try:
        db._conn.execute("UPDATE wt_task_events SET event_type='x' WHERE task_id=?", (a.task_id,)); db._conn.commit()
    except sqlite3.IntegrityError:
        up_blocked = True
    try:
        db._conn.execute("DELETE FROM wt_task_events WHERE task_id=?", (a.task_id,)); db._conn.commit()
    except sqlite3.IntegrityError:
        del_blocked = True
    ok &= check("۱۶) UPDATE روی audit رد می‌شود", up_blocked)
    ok &= check("۱۷) DELETE روی audit رد می‌شود", del_blocked)
    return ok


def test_audit_fields_no_secrets():
    _fresh_db()
    a = ts.create_task(_admin_ctx("task_create", "telegram:3:c", actor=111),
                       222, "علی", "مدیر", "متنِ محرمانهٔ تسک ۰۹۱۲۳۴۵۶۷۸۹")
    row = db._conn.execute("SELECT actor_id, actor_role, source, idempotency_key, prev_json, new_json, occurred_at "
                           "FROM wt_task_events WHERE task_id=?", (a.task_id,)).fetchone()
    actor_id, actor_role, source, idem, prev_json, new_json, occurred = row
    blob = f"{prev_json}{new_json}"
    ok = check("۱۸) actor/source/idempotency-key ثبت شدند",
               actor_id == 111 and actor_role == "admin" and source == "telegram" and idem == "telegram:3:c")
    ok &= check("۱۹) متنِ خامِ تسک/شماره در audit نیست (فقط طول/هش)",
                "محرمانه" not in blob and "09123456789" not in blob and "۰۹۱۲۳۴۵۶۷۸۹" not in blob)
    ok &= check("۲۰) timestampِ UTC معتبر", isinstance(occurred, str) and occurred.endswith(("Z", "+00:00")))
    return ok


# ---------------- authorization ----------------
def test_authz():
    _fresh_db()
    ok = True
    # 21: بدونِ actor معتبر → رد
    bad = ts.create_task(ts.MutationContext(actor_id=0, actor_role="staff", source="telegram",
                                            operation="task_create"), 1, "x", "y", "z")
    ok &= check("۲۱) context نامعتبر (staff بدونِ actor) → رد", bad.status == "invalid")
    # 22: staff نتواند تسک بسازد (عملیاتِ مدیریتی) با فراخوانیِ مستقیم
    st = ts.create_task(_staff_ctx("task_create", 222), 222, "x", "y", "z")
    ok &= check("۲۲) پرسنل نمی‌تواند مستقیم تسک بسازد", st.status == "unauthorized" and _count("wt_tasks") == 0)
    # cross-user close: staff فقط تسکِ خودش
    a = ts.create_task(_admin_ctx("task_create", "k"), 222, "علی", "مدیر", "کار")
    other = ts.mark_done(_staff_ctx("task_mark_done", 999), a.task_id)   # 999 مالک نیست
    ok &= check("۲۲ب) staff تسکِ دیگری را نمی‌بندد", other.status == "unauthorized")
    mine = ts.mark_done(_staff_ctx("task_mark_done", 222), a.task_id)     # 222 مالک است
    ok &= check("۲۲ج) staff تسکِ خودش را می‌بندد", mine.status == "applied")
    return ok


def test_authz_hallucinated_and_bad_op():
    _fresh_db()
    ok = True
    # 25: task ID hallucinated → not_found، بدونِ mutation
    r = ts.mark_done(_admin_ctx("task_mark_done", "k"), 999999)
    ok &= check("۲۵) task ID توهمی اجرا نمی‌شود (not_found)", r.status == "not_found"
                and _count("wt_task_events") == 0)
    # 24: operation خارج از allowlist → invalid
    bad = ts.create_task(_admin_ctx("frobnicate"), 1, "x", "y", "z")
    ok &= check("۲۴) operation خارج از allowlist رد می‌شود", bad.status == "invalid")
    return ok


def test_ambiguous_name_guard():
    _fresh_db()
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (1,'مریم اکبری',0,0)")
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (2,'مریم رضایی',0,0)")
    db._conn.commit()
    return check("۲۶) نامِ چندمعنا («مریم») → بیش از یک match (mutation نباید بسازد)", w._name_matches("مریم") == 2
                 and w._name_matches("مریم اکبری") == 1)


# ---------------- transaction / no network in tx ----------------
def test_no_network_in_service():
    """۲۷) هیچ فراخوانیِ شبکه/LLM داخلِ سرویسِ mutation نیست (کدِ سرویس فقط sqlite + stdlib)."""
    import inspect
    src = inspect.getsource(ts)
    bad = any(tok in src for tok in ("requests.", "httpx", "urllib.request", "await ", "aiohttp", "openai"))
    return check("۲۷) سرویسِ mutation هیچ network/async call ندارد (تراکنش کوتاه، بدونِ I/O شبکه)", not bad)


# ---------------- regression ----------------
def test_regression_create_and_done():
    _fresh_db()
    ok = True
    tid = w._add_task(222, "علی", 111, "مدیر", "کارِ تست")   # مسیرِ فعلی
    ok &= check("۳۱) رفتارِ create task حفظ شد (id بازمی‌گردد، open)", isinstance(tid, int) and tid > 0
                and db._conn.execute("SELECT status FROM wt_tasks WHERE id=?", (tid,)).fetchone()[0] == "open")
    done = w._task_done(tid, 222)   # مالک می‌بندد
    ok &= check("۳۲) رفتارِ mark done حفظ شد (owner→True، done)", done is True
                and db._conn.execute("SELECT status FROM wt_tasks WHERE id=?", (tid,)).fetchone()[0] == "done")
    not_owner = w._task_done(tid, 999)
    ok &= check("۳۲ب) غیرمالک/بسته → False", not_owner is False)
    # crawl dedup هنوز کار می‌کند (source_key)
    a = w._add_task(0, "—", 0, "sys", "مشکل", source_key="K1")
    b = w._add_task(0, "—", 0, "sys", "مشکل", source_key="K1")
    ok &= check("۳۳) dedupِ خزش (source_key) حفظ شد (دومی -1)", a > 0 and b == -1)
    return ok


def main():
    tests = [test_idem_create_and_inbound, test_idem_concurrent_claim, test_idem_lease_recovery,
             test_idem_retry_caps, test_idem_key_conflict, test_mark_done_idempotent,
             test_audit_atomic_and_rollback, test_audit_append_only, test_audit_fields_no_secrets,
             test_authz, test_authz_hallucinated_and_bad_op, test_ambiguous_name_guard,
             test_no_network_in_service, test_regression_create_and_done]
    results = []
    for t in tests:
        try:
            results.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            results.append(False)
    passed, total = sum(results), len(results)
    print(f"\n{passed}/{total} گروهِ تستِ فاز ۱ سبز شد.")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
