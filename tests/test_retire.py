"""قطعِ همکاریِ سبک (retire) — تستِ آفلاین. مستقل از HR flag؛ بادوام؛ سابقه‌حفظ؛ برگشت‌پذیر."""
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

_ok = True


def check(name, cond):
    global _ok
    _ok = _ok and bool(cond)
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def _fresh(personnel=False):
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()
    config.ADMIN_USER_IDS = [111]
    config.WT_PRIMARY_ADMIN_ID = 111
    config.WT_LIFECYCLE_ENABLED = True
    config.WT_MANAGER_VERIFICATION_ENABLED = True
    config.WT_PERSONNEL_ENABLED = personnel  # retire باید مستقل از این کار کند


def _seed_worker(uid=222, name="اسما"):
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind) VALUES (?,?,?,?,?, 'work')",
                     (uid, name, "2026-07-18", "t", time.time()))
    db._conn.commit()


def t1_retire_excludes_from_reminders():
    _fresh()
    _seed_worker(222)
    before = [u for u, _ in w.workers_without_report("2026-07-20")]
    w._set_retired(111, 222, "اسما", True)
    after = [u for u, _ in w.workers_without_report("2026-07-20")]
    return check("1) retire → از یادآوری/کارتِ عملکرد حذف می‌شود", 222 in before and 222 not in after)


def t2_retired_no_new_task():
    _fresh()
    _seed_worker(222)
    w._set_retired(111, 222, "اسما", True)
    tid = w._add_task(222, "اسما", 111, "مدیر", "کارِ جدید")
    return check("2) پرسنلِ قطع‌همکاری تسکِ جدید نمی‌گیرد", tid == -1)


def t3_retired_blocks_lifecycle():
    _fresh()
    _seed_worker(222)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="k")
    t = ts.create_task(ctx, 222, "اسما", "مدیر", "کار", task_kind="staff", lifecycle_state="open").task_id
    w._set_retired(111, 222, "اسما", True)
    r = w.lifecycle_start(t, 222)
    return check("3) پرسنلِ قطع‌همکاری اکشنِ چرخه نمی‌تواند", r.status == "unauthorized")


def t4_unretire_restores():
    _fresh()
    _seed_worker(222)
    w._set_retired(111, 222, "اسما", True)
    w._set_retired(111, 222, "اسما", False)
    back = [u for u, _ in w.workers_without_report("2026-07-20")]
    tid = w._add_task(222, "اسما", 111, "مدیر", "کارِ جدید")
    return check("4) unretire → دوباره فعال (worker + تسک می‌گیرد)", 222 in back and tid > 0)


def t5_independent_of_hr_flag():
    _fresh(personnel=False)  # HR خاموش
    _seed_worker(222)
    w._set_retired(111, 222, "اسما", True)
    excluded = 222 not in [u for u, _ in w.workers_without_report("2026-07-20")]
    return check("5) retire مستقل از WT_PERSONNEL_ENABLED کار می‌کند", excluded)


def t6_history_preserved_and_audited():
    _fresh()
    _seed_worker(222)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="k2")
    ts.create_task(ctx, 222, "اسما", "مدیر", "کارِ قبلی", task_kind="staff", lifecycle_state="open")
    w._set_retired(111, 222, "اسما", True)
    staff = db._conn.execute("SELECT COUNT(*) FROM wt_staff WHERE user_id=222").fetchone()[0]
    reports = db._conn.execute("SELECT COUNT(*) FROM wt_reports WHERE user_id=222").fetchone()[0]
    tasks = db._conn.execute("SELECT COUNT(*) FROM wt_tasks WHERE assignee_id=222").fetchone()[0]
    audit = db._conn.execute("SELECT COUNT(*) FROM wt_hr_events WHERE entity_id=222 AND event_type='staff_retired'").fetchone()[0]
    return check("6) سابقه حفظ (staff/reports/tasks) + auditِ retire ثبت شد",
                 staff == 1 and reports == 1 and tasks == 1 and audit == 1)


def t7_open_tasks_still_visible_to_manager():
    _fresh()
    _seed_worker(222)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="k3")
    ts.create_task(ctx, 222, "اسما", "مدیر", "کارِ باز", task_kind="staff", lifecycle_state="open")
    w._set_retired(111, 222, "اسما", True)
    openrows = w._open_tasks(222)   # تسک‌های بازش هنوز برای مدیر قابلِ دیدن‌اند (حذف نشدند)
    return check("7) تسک‌های بازِ فردِ قطع‌همکاری همچنان موجود/قابلِ‌نمایش (حذف نشدند)", len(openrows) == 1)


def main():
    tests = [t1_retire_excludes_from_reminders, t2_retired_no_new_task, t3_retired_blocks_lifecycle,
             t4_unretire_restores, t5_independent_of_hr_flag, t6_history_preserved_and_audited,
             t7_open_tasks_still_visible_to_manager]
    res = []
    for t in tests:
        try:
            res.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            res.append(False)
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ تستِ retire سبز شد؛ همهٔ assertها: {'✅' if _ok else '❌'}")
    sys.exit(0 if (p == n and _ok) else 1)


if __name__ == "__main__":
    main()
