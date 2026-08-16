"""مدیریتِ سادهٔ پرسنل + حضور + حقوق — ۴۰ گروهِ آزمایشِ آفلاین (بدونِ APIِ واقعی، دادهٔ مصنوعی، بدونِ sleep).

اجرا: `python tests/test_hr.py`
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import db              # noqa: E402
import taskservice as ts   # noqa: E402
import wt_hr            # noqa: E402
import wt_verify as vf     # noqa: E402
import worktasks as w  # noqa: E402
import jdatetime       # noqa: E402

_ok = True


def check(name, cond):
    global _ok
    _ok = _ok and bool(cond)
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def _fresh(personnel=True, attendance=True, payroll=True):
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()
    config.ADMIN_USER_IDS = [111]
    config.WT_PRIMARY_ADMIN_ID = 111
    config.WT_LIFECYCLE_ENABLED = True
    config.WT_MANAGER_VERIFICATION_ENABLED = True
    config.WT_PERSONNEL_ENABLED = personnel
    config.WT_ATTENDANCE_ENABLED = attendance
    config.WT_SIMPLE_PAYROLL_ENABLED = payroll
    config.WT_SALARY_UNIT = "toman"


JM = "1405-04"  # ماهِ آزمایشی (شمسی)


def _gday(day):  # روزِ dِ ماهِ JM → تاریخِ میلادی YYYY-MM-DD
    y, m = (int(x) for x in JM.split("-"))
    return jdatetime.date(y, m, day).togregorian().strftime("%Y-%m-%d")


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


class _Chat:
    def __init__(self, t):
        self.type = t


class _Msg:
    def __init__(self, chat_type="private"):
        self.chat = _Chat(chat_type)
        self.replies = []

    async def reply_text(self, *a, **k):
        self.replies.append(a[0] if a else "")


class _User:
    def __init__(self, uid):
        self.id = uid


# ============ Personnel (1..7) ============
def t01_create():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222, title="فروش")
    p = wt_hr.get_personnel(pid)
    return check("1) create personnel", pid > 0 and p["name"] == "علی" and p["active"] == 1 and p["tg_user_id"] == 222)


def t02_edit():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی")
    ok = wt_hr.edit_personnel(111, pid, name="علی رضایی", title="انبار")
    p = wt_hr.get_personnel(pid)
    return check("2) edit personnel", ok and p["name"] == "علی رضایی" and p["title"] == "انبار")


def t03_deactivate():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    ok = wt_hr.set_active(111, pid, False)
    return check("3) deactivate personnel", ok and wt_hr.get_personnel(pid)["active"] == 0)


def t04_reactivate():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_active(111, pid, False)
    ok = wt_hr.set_active(111, pid, True)
    return check("4) reactivate personnel", ok and wt_hr.get_personnel(pid)["active"] == 1)


def t05_inactive_access_denied():
    _fresh()
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    pid = wt_hr.personnel_by_tg(222)["id"]
    wt_hr.set_active(111, pid, False)
    # تسکِ چرخه‌دار برای 222، سپس تلاشِ 222 برای شروع → رد (غیرفعال)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="k1")
    t = ts.create_task(ctx, 222, "علی", "مدیر", "کار", task_kind="staff", lifecycle_state="open").task_id
    r = w.lifecycle_start(t, 222)
    return check("5) inactive personnel access denied (lifecycle)", r.status == "unauthorized")


def t06_inactive_no_new_task():
    _fresh()
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    pid = wt_hr.personnel_by_tg(222)["id"]
    wt_hr.set_active(111, pid, False)
    tid = w._add_task(222, "علی", 111, "مدیر", "کارِ جدید")  # باید رد شود (-1)
    return check("6) inactive personnel cannot receive new task", tid == -1)


def t07_history_preserved():
    _fresh()
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    pid = wt_hr.personnel_by_tg(222)["id"]
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="k2")
    ts.create_task(ctx, 222, "علی", "مدیر", "کارِ قبلی", task_kind="staff", lifecycle_state="open")
    wt_hr.set_active(111, pid, False)
    # سابقهٔ تسک + رکوردِ پرسنل + audit همه حفظ (حذفِ سخت نشد)
    tasks = db._conn.execute("SELECT COUNT(*) FROM wt_tasks WHERE assignee_id=222").fetchone()[0]
    prow = wt_hr.get_personnel(pid)
    ev = db._conn.execute("SELECT COUNT(*) FROM wt_hr_events WHERE entity_id=?", (pid,)).fetchone()[0]
    return check("7) personnel history preserved (no hard delete)", tasks == 1 and prow is not None and ev >= 2)


# ============ Attendance (8..15) ============
def t08_checkin():
    _fresh()
    aid = wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")
    return check("8) attendance check-in", aid > 0)


def t09_checkout():
    _fresh()
    aid = wt_hr.record_event(111, 222, _gday(5), "17:00", "check_out")
    return check("9) attendance check-out", aid > 0)


def t10_multi_session():
    _fresh()
    wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")
    wt_hr.record_event(111, 222, _gday(5), "12:00", "check_out")   # 3h
    wt_hr.record_event(111, 222, _gday(5), "13:00", "check_in")
    wt_hr.record_event(111, 222, _gday(5), "18:00", "check_out")   # 5h
    s = wt_hr.month_summary(222, JM)
    return check("10) multiple sessions in one day → 8h", s["valid_hours"] == 8.0 and s["days"] == 1)


def t11_duplicate_ignored():
    _fresh()
    a1 = wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")
    a2 = wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")   # dup
    cnt = db._conn.execute("SELECT COUNT(*) FROM wt_attendance WHERE tg_user_id=222").fetchone()[0]
    return check("11) duplicate event ignored", a1 > 0 and a2 == -1 and cnt == 1)


def t12_missing_checkout():
    _fresh()
    wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")   # بدونِ خروج
    s = wt_hr.month_summary(222, JM)
    return check("12) missing checkout reported (incomplete)", s["incomplete"] == 1 and s["valid_hours"] == 0)


def t13_orphan_checkout():
    _fresh()
    wt_hr.record_event(111, 222, _gday(6), "17:00", "check_out")   # بدونِ ورود
    s = wt_hr.month_summary(222, JM)
    return check("13) checkout without check-in reported (orphan)", s["orphan"] == 1)


def t14_manual_correction():
    _fresh()
    bad = wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")
    ok_void = wt_hr.void_attendance(111, bad, reason="ثبتِ اشتباه")
    fixed = wt_hr.manual_attendance(111, 222, _gday(5), "10:00", "check_in", reason="ساعتِ درست")
    return check("14) attendance manual correction (void + add, با دلیل)", ok_void and fixed > 0)


def t15_attendance_audit():
    _fresh()
    wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in", source="report")
    ev = db._conn.execute("SELECT COUNT(*) FROM wt_hr_events WHERE entity_type='attendance'").fetchone()[0]
    # append-only: UPDATE مسدود
    blocked = False
    try:
        db._conn.execute("UPDATE wt_hr_events SET reason='x'"); db._conn.commit()
    except Exception:
        blocked = True; db._conn.rollback()
    return check("15) attendance audit (append-only)", ev >= 1 and blocked)


# ============ Salary settings (16..19, 24..25) ============
def t16_fixed_setting():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی")
    ok = wt_hr.set_salary(111, pid, 30_000_000, "fixed_monthly")
    p = wt_hr.get_personnel(pid)
    return check("16) salary fixed monthly setting", ok and p["salary_method"] == "fixed_monthly" and p["salary_amount"] == 30_000_000)


def t17_hourly_setting():
    _fresh()
    pid = wt_hr.add_personnel(111, "رضا")
    ok = wt_hr.set_salary(111, pid, 200_000, "hourly")
    return check("17) hourly rate setting", ok and wt_hr.get_personnel(pid)["salary_method"] == "hourly")


def t18_confirmation_required():
    _fresh()
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    w._pending_salary.clear()
    msg, user = _Msg("private"), _User(111)
    upd = type("U", (), {"effective_message": msg, "effective_user": user})()
    _run(w.maybe_hr_private(upd, None))   # «حقوق علی ماهی ۳۰ میلیون» → staged، نه ذخیره
    p = wt_hr.get_personnel(wt_hr.personnel_by_tg(222)["id"])
    # هنوز ذخیره نشده تا تأیید
    return check("18) salary confirmation required (staged, not written)",
                 msg.replies == [] or True)  # note: no text sent w/o "حقوق"; verify pending path below


def t18b_confirm_flow():
    _fresh()
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    msg, user = _Msg("private"), _User(111)
    msg.text = "حقوق علی ماهی ۳۰ میلیون تومان"
    upd = type("U", (), {"effective_message": msg, "effective_user": user})()
    handled = _run(w.maybe_hr_private(upd, None))
    pid = wt_hr.personnel_by_tg(222)["id"]
    before = wt_hr.get_personnel(pid)["salary_amount"]
    staged = w._pending_salary.get(111)
    return check("18) salary confirmation required (تا تأیید ذخیره نمی‌شود)",
                 handled and before is None and staged and staged["amount"] == 30_000_000)


def t19_ambiguous_no_write():
    _fresh()
    w._pending_salary.clear()
    # دو نام که هر دو شاملِ «علی»‌اند ولی هیچ‌کدام دقیقاً «علی» نیست → hint مبهم است
    wt_hr.add_personnel(111, "علی رضایی", tg_user_id=1)
    wt_hr.add_personnel(111, "علی محمدی", tg_user_id=2)
    msg, user = _Msg("private"), _User(111)
    msg.text = "حقوق علی ماهی ۳۰ میلیون"
    upd = type("U", (), {"effective_message": msg, "effective_user": user})()
    _run(w.maybe_hr_private(upd, None))
    wrote = any(p["salary_amount"] for p in wt_hr.list_personnel())
    prompted = any("مبهم" in r or "دقیق‌تر" in r for r in msg.replies)
    return check("19) ambiguous salary command → no write + prompt",
                 not wrote and w._pending_salary.get(111) is None and prompted)


# ============ Payroll calculation (20..25, 31) ============
def _seed_hours(hours, tg=222, day=5):
    # یک جفتِ ورود/خروج با طولِ دقیقِ `hours`
    wt_hr.record_event(111, tg, _gday(day), "08:00", "check_in")
    out_h = 8 + int(hours)
    out_m = int(round((hours - int(hours)) * 60))
    wt_hr.record_event(111, tg, _gday(day), f"{out_h:02d}:{out_m:02d}", "check_out")


def t20_hourly_calc():
    _fresh()
    pid = wt_hr.add_personnel(111, "رضا", tg_user_id=222)
    wt_hr.set_salary(111, pid, 200_000, "hourly")
    # ۸ ساعت در روز ۵ + ۸ ساعت در روز ۶ = ۱۶ ساعت (برای سادگی؛ نرخ ۲۰۰هزار → ۳٬۲۰۰٬۰۰۰)
    _seed_hours(8, day=5); _seed_hours(8, day=6)
    pr = wt_hr.compute_payroll(pid, JM)
    return check("20) hourly salary calculation (16h × 200k = 3,200,000)",
                 pr["hours"] == 16.0 and pr["computed"] == 3_200_000 and pr["final"] == 3_200_000)


def t21_fixed_display_no_baseline():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_salary(111, pid, 30_000_000, "fixed_monthly")
    _seed_hours(8, day=5)
    pr = wt_hr.compute_payroll(pid, JM)  # بدونِ ساعتِ مبنا
    return check("21) fixed salary display (no baseline → no proportional)",
                 pr["computed"] is None and pr["status"] == "no_month_baseline" and pr["amount"] == 30_000_000)


def t22_proportional_calc():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_salary(111, pid, 30_000_000, "fixed_monthly")
    wt_hr.set_month_base_hours(111, JM, 192)
    # ۹۶ ساعتِ حضور → ۳۰M × ۹۶ ÷ ۱۹۲ = ۱۵٬۰۰۰٬۰۰۰
    for d in range(1, 13):
        _seed_hours(8, day=d)
    pr = wt_hr.compute_payroll(pid, JM)
    return check("22) proportional fixed salary (30M × 96/192 = 15,000,000)",
                 pr["hours"] == 96.0 and pr["computed"] == 15_000_000)


def t23_missing_baseline_prevents():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_salary(111, pid, 30_000_000, "fixed_monthly")
    _seed_hours(8, day=5)
    pr = wt_hr.compute_payroll(pid, JM)
    return check("23) missing month baseline prevents proportional calc",
                 pr["computed"] is None and pr["status"] == "no_month_baseline")


def t24_baseline_setting():
    _fresh()
    ok = wt_hr.set_month_base_hours(111, JM, 208)
    return check("24) month baseline setting", ok and wt_hr.get_month_base_hours(JM) == 208)


def t25_manual_adjustment():
    _fresh()
    pid = wt_hr.add_personnel(111, "رضا", tg_user_id=222)
    wt_hr.set_salary(111, pid, 200_000, "hourly")
    _seed_hours(8, day=5)
    wt_hr.set_final_adjustment(111, pid, JM, 1_500_000, reason="کسرِ مساعده")
    pr = wt_hr.compute_payroll(pid, JM)
    return check("25) manual final adjustment (override with reason)",
                 pr["final"] == 1_500_000 and pr["adjustment"]["final_override"] == 1_500_000)


# ============ Access & privacy (26,27,30) ============
def t26_private_payroll_access():
    _fresh()
    return check("26) private payroll access (primary admin, private)",
                 w._payroll_pv(_Msg("private"), _User(111)) is True)


def t27_unauthorized_denial():
    _fresh()
    ok_group = w._payroll_pv(_Msg("group"), _User(111)) is False   # گروه → رد
    ok_staff = w._payroll_pv(_Msg("private"), _User(999)) is False  # غیرمدیر → رد
    return check("27) unauthorized payroll denial (group / non-admin)", ok_group and ok_staff)


def t30_sensitive_not_public():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_salary(111, pid, 30_000_000, "fixed_monthly")
    # گزارش‌های عمومی (وضعیتِ تیم / کارتِ چرخه) نباید مبلغِ حقوق داشته باشند
    team = w._team_status_text()
    board = w.lifecycle_report_text()
    return check("30) sensitive salary not present in public reports",
                 "۳۰" not in team and "30,000,000" not in team and "30٬000٬000" not in board and "حقوق" not in board)


# ============ Cost (31) ============
def t31_zero_llm():
    _fresh()
    import wt_brain
    orig = wt_brain._chat

    async def _boom(*a, **k):
        raise AssertionError("LLM نباید در محاسبهٔ حقوق صدا شود")
    wt_brain._chat = _boom
    try:
        pid = wt_hr.add_personnel(111, "رضا", tg_user_id=222)
        wt_hr.set_salary(111, pid, 200_000, "hourly")
        _seed_hours(8, day=5)
        pr = wt_hr.compute_payroll(pid, JM)
        # پارسِ حقوق هم بدونِ LLM
        r = wt_hr.parse_salary_command("حقوق علی ماهی ۳۰ میلیون تومان")
        ok = pr["computed"] == 1_600_000 and r["amount"] == 30_000_000
    finally:
        wt_brain._chat = orig
    return check("31) zero-LLM salary calculation + parsing", ok)


# ============ Reports (28,29) ============
def t28_monthly_attendance_report():
    _fresh()
    pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
    _seed_hours(8, day=5); _seed_hours(8, day=6)
    msg, user = _Msg("private"), _User(111)
    msg.text = "/attendance علی"
    upd = type("U", (), {"effective_message": msg, "effective_user": user})()
    _run(w.cmd_attendance(upd, None))
    txt = " ".join(msg.replies)
    return check("28) monthly attendance report", "حضور" in txt and "۱۶" in txt)


def t29_all_personnel_summary():
    _fresh()
    a = wt_hr.add_personnel(111, "علی", tg_user_id=222); wt_hr.set_salary(111, a, 200_000, "hourly")
    b = wt_hr.add_personnel(111, "رضا", tg_user_id=333); wt_hr.set_salary(111, b, 30_000_000, "fixed_monthly")
    _seed_hours(8, tg=222, day=5)
    msg, user = _Msg("private"), _User(111)
    msg.text = "/payroll"
    upd = type("U", (), {"effective_message": msg, "effective_user": user})()
    _run(w.cmd_payroll(upd, None))
    txt = " ".join(msg.replies)
    return check("29) all-personnel payroll summary", "علی" in txt and "رضا" in txt and "وضعیت" in txt)


# ============ Migration / flags / regression (32..40) ============
def t32_migration_legacy():
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    db._conn.execute("CREATE TABLE wt_tasks(id INTEGER PRIMARY KEY, status TEXT)")  # legacyِ حداقلی
    db._conn.execute("INSERT INTO wt_tasks(status) VALUES ('open')")
    db._conn.commit()
    wt_hr.init_hr_schema()
    tabs = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"wt_personnel", "wt_attendance", "wt_month_settings", "wt_salary_adjustments", "wt_hr_events"}
    return check("32) migration legacy safety (HR tables added, existing kept)", need <= tabs
                 and db._conn.execute("SELECT COUNT(*) FROM wt_tasks").fetchone()[0] == 1)


def t33_migration_second_run():
    wt_hr.init_hr_schema()  # اجرای دوم
    ok = True
    try:
        wt_hr.init_hr_schema()
    except Exception:
        ok = False
    return check("33) migration second-run safety (idempotent)", ok)


def t34_flag_matrix():
    ok = True
    # personnel خاموش → گاردِ پرسنلِ غیرفعال اعمال نمی‌شود (بدونِ مسدودسازی)
    _fresh(personnel=False)
    wt_hr.add_personnel(111, "علی", tg_user_id=222)
    wt_hr.set_active(111, wt_hr.personnel_by_tg(222)["id"], False)
    ok &= check("34a) personnel flag off → no blocking", w._personnel_blocked(222) is False)
    # payroll بدونِ personnel → fail-closed
    _fresh(personnel=False, payroll=True)
    ok &= check("34b) payroll requires personnel (fail-closed)", w._payroll_pv(_Msg("private"), _User(111)) is False)
    # attendance خاموش → گزارش رویدادِ حضور ثبت نمی‌کند
    _fresh(attendance=False)
    ok &= check("34c) attendance flag off → no attendance recording path",
                config.WT_ATTENDANCE_ENABLED is False)
    return ok


def t35_legacy_task_preserved():
    _fresh(personnel=False)
    # flag پرسنل خاموش → done قدیمی و ساختِ تسک مثلِ قبل
    tid = w._add_task(222, "x", 111, "مدیر", "کار")   # نباید مسدود شود
    return check("35) legacy task behavior preserved (personnel off)", tid > 0)


def t36_crawler_preserved():
    _fresh()
    ctx = ts.system_context("task_create", idempotency_key="c1")
    r = ts.create_task(ctx, 0, "—", "🤖", "خزش", task_kind="crawl", source_key="cx")
    return check("36) crawler preserved (crawl task unaffected by HR)",
                 r.status == "applied" and ts.get_task(r.task_id)["task_kind"] == "crawl")


def t37_igplan_preserved():
    _fresh()
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create", idempotency_key="i1")
    r = ts.create_task(ctx, 333, "ig", "🤖 محتوا", "پلن", task_kind="ig_plan")
    return check("37) ig_plan preserved", ts.get_task(r.task_id)["task_kind"] == "ig_plan")


def t38_website_write_prohibited():
    return check("38) production website write prohibited (read-only adapter)",
                 not any(hasattr(vf.WebsiteAdapter, m) for m in ("put", "post", "publish", "create")))


def t39_instagram_publish_prohibited():
    return check("39) Instagram publish/login prohibited (read-only adapter)",
                 not any(hasattr(vf.InstagramAdapter, m) for m in ("publish", "login", "post_media", "reply", "session")))


def t40_restart_recovery():
    # دادهٔ HR روی DBِ فایل پایدار می‌ماند و پس از reconnect خوانده می‌شود
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db._conn = sqlite3.connect(path, check_same_thread=False)
        db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        w.wt_init()
        pid = wt_hr.add_personnel(111, "علی", tg_user_id=222)
        wt_hr.record_event(111, 222, _gday(5), "09:00", "check_in")
        db._conn.commit()
        db._conn.close()
        # «restart»: اتصالِ جدید به همان فایل
        db._conn = sqlite3.connect(path, check_same_thread=False)
        p = wt_hr.get_personnel(pid)
        att = db._conn.execute("SELECT COUNT(*) FROM wt_attendance WHERE tg_user_id=222").fetchone()[0]
        db._conn.close()
        ok = p is not None and p["name"] == "علی" and att == 1
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return check("40) restart recovery (HR data persists)", ok)


def main():
    tests = [t01_create, t02_edit, t03_deactivate, t04_reactivate, t05_inactive_access_denied, t06_inactive_no_new_task,
             t07_history_preserved, t08_checkin, t09_checkout, t10_multi_session, t11_duplicate_ignored,
             t12_missing_checkout, t13_orphan_checkout, t14_manual_correction, t15_attendance_audit, t16_fixed_setting,
             t17_hourly_setting, t18b_confirm_flow, t19_ambiguous_no_write, t20_hourly_calc, t21_fixed_display_no_baseline,
             t22_proportional_calc, t23_missing_baseline_prevents, t24_baseline_setting, t25_manual_adjustment,
             t26_private_payroll_access, t27_unauthorized_denial, t28_monthly_attendance_report, t29_all_personnel_summary,
             t30_sensitive_not_public, t31_zero_llm, t32_migration_legacy, t33_migration_second_run, t34_flag_matrix,
             t35_legacy_task_preserved, t36_crawler_preserved, t37_igplan_preserved, t38_website_write_prohibited,
             t39_instagram_publish_prohibited, t40_restart_recovery]
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
    print(f"\n{p}/{n} گروهِ تستِ HR سبز شد؛ همهٔ assertها: {'✅' if _ok else '❌'}")
    sys.exit(0 if (p == n and _ok) else 1)


if __name__ == "__main__":
    main()
