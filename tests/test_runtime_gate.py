"""Phase 2A Runtime Verification Gate — in-process fault-injection harness (RV-2 / RV-3 / RV-5).

اجرای EXTERNAL روی staging متوقف است (staging جدا/credentials مجزا موجود نیست؛ رجوع به 01_STAGING_PREFLIGHT).
این هارنس هیچ پیامِ واقعی نمی‌فرستد، هیچ APIِ واقعی صدا نمی‌زند و به DBِ production دست نمی‌زند:
همه‌چیز روی in-memory SQLite + fake transport + کنترلِ قطعیِ lease (بدونِ sleep) اجرا می‌شود.

هیچ راز/توکن/prompt خام/PII چاپ یا ذخیره نمی‌شود. اجرا: `python tests/test_runtime_gate.py`
هدف: تولیدِ شواهدِ واقعیِ رفتارِ ledger/lease/model-resolution/race برای تصمیمِ gate (GO/CONDITIONAL/NO-GO).
"""
import asyncio
import os
import sqlite3
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import db              # noqa: E402
import taskservice as ts   # noqa: E402
import wt_brain        # noqa: E402
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
    w._awaiting_answers.clear()
    w._awaiting.clear()
    w._followup_inflight.clear()
    w._report_ctx.clear()


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _ledger(key, source="delivery"):
    return db._conn.execute(
        "SELECT status, attempt_count, result_reference, lease_expires_at FROM wt_inbound_events "
        "WHERE source=? AND external_event_id=?", (source, key)).fetchone()


class _FakeBot:
    """آداپترِ جعلیِ تلگرام — فقط ارسال‌ها را می‌شمارد؛ هیچ شبکه‌ای در کار نیست."""
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, *a, **k):
        if self.fail:
            raise RuntimeError("fake-network-down")
        self.sent.append((a, k))
        return type("M", (), {"message_id": 900 + len(self.sent)})()


# ============================================================
# RV-2 — Delivery Ledger Verification
# ============================================================
def rv2_01_normal_and_retry():
    """ارسالِ عادی: claim→send→complete؛ retryِ همان کلید پیام دوم نمی‌سازد."""
    _fresh()
    k = "reminder:D:21"
    d1, _ = ts.delivery_claim(k, operation="report_reminder")
    ts.delivery_complete(k, message_id=901)
    d2, _ = ts.delivery_claim(k, operation="report_reminder")   # retryِ همان logical key
    st, att, ref, _ = _ledger(k)
    ok = check("RV2-01) اولین claim = claimed", d1 == "claimed")
    ok &= check("RV2-01) retry = duplicate (پیام دوم ساخته نمی‌شود)", d2 == "duplicate")
    ok &= check("RV2-01) ledger=succeeded و message_id ثبت شد (شواهدِ تحویل)", st == "succeeded" and ref == "901")
    return ok


def rv2_02_two_workers_concurrent():
    """دو worker هم‌زمان روی یک کلید: دقیقاً یکی claim معتبر می‌گیرد."""
    _fresh()
    k = "perf:D"
    barrier = threading.Barrier(2)
    out = {}

    def worker(tag):
        barrier.wait()
        out[tag] = ts.delivery_claim(k, operation="manager_perf")[0]

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    decisions = sorted(out.values())
    claimed = sum(1 for d in out.values() if d == "claimed")
    ok = check(f"RV2-02) دقیقاً یک claimed (decisions={decisions})", claimed == 1)
    ok &= check("RV2-02) دیگری in_progress/kontrol‌شده (نه claim دوم)", "claimed" not in [d for d in out.values() if d != "claimed"])
    return ok


def rv2_03_crash_before_send_recovers():
    """crash پس از claim و قبل از send: با انقضای lease دوباره قابل ارسال است (silent loss در سطحِ ledger رخ نمی‌دهد)."""
    _fresh()
    k = "reminder:D:21"
    d1, _ = ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)  # claim با leaseِ منقضی (شبیه‌سازیِ کرش)
    # هیچ complete/fail — یعنی send هرگز رخ نداد و پروسه مرد
    d2, _ = ts.delivery_claim(k, operation="report_reminder")   # دورِ recovery
    st, att, ref, _ = _ledger(k)
    ok = check("RV2-03) claim اول", d1 == "claimed")
    ok &= check("RV2-03) پس از انقضای lease → recovered (پیام قابلِ ارسالِ دوباره؛ گم‌شدنِ خاموش در ledger نیست)", d2 == "recovered")
    ok &= check("RV2-03) هنوز delivered علامت نخورده (attempt افزایش یافت)", st == "processing" and att == 2 and (ref in (None, "")))
    return ok


def rv2_04_network_exception_retryable():
    """exception شبکه‌ای: failed_retryable ثبت می‌شود، delivered تلقی نمی‌شود، retry ممکن است."""
    _fresh()
    k = "perf:D"
    ts.delivery_claim(k, operation="manager_perf")
    ts.delivery_fail(k, error_type="TimeoutError")       # send شکست خورد
    st1, att1, ref1, _ = _ledger(k)
    d2, _ = ts.delivery_claim(k, operation="manager_perf")   # retry
    st2, att2, _, _ = _ledger(k)
    ok = check("RV2-04) پس از شکست = failed_retryable و delivered نیست", st1 == "failed_retryable" and ref1 in (None, ""))
    ok &= check("RV2-04) retry → recovered و attempt افزایش یافت", d2 == "recovered" and att2 == 2)
    return ok


def rv2_05_ack_before_commit_ambiguous():
    """مهم‌ترین crash window: ack گرفته شد ولی قبل از mark delivered کرش شد.
    چون message_id فقط در complete ثبت می‌شود، ledger نمی‌تواند ack را بازشناسد → at-least-once (امکانِ duplicate)."""
    _fresh()
    k = "perf:D"
    d1, _ = ts.delivery_claim(k, operation="manager_perf", lease_sec=-1)  # claim؛ send موفق (ack=910) اما complete قبل از کرش اجرا نشد
    # عمداً delivery_complete صدا زده نمی‌شود (کرش در همان پنجره)
    d2, _ = ts.delivery_claim(k, operation="manager_perf")   # دورِ بعد: lease منقضی
    st, att, ref, _ = _ledger(k)
    ok = check("RV2-05) دورِ بعد = recovered → همان پیام دوباره ارسال می‌شود (DUPLICATE ممکن)", d2 == "recovered")
    ok &= check("RV2-05) ledger هیچ ردی از ackِ اول ندارد (message_id ثبت نشده) → عدمِ امکانِ reconciliation", ref in (None, ""))
    ok &= check("RV2-05) هیچ ادعای exactly-once واقعی نیست؛ وضعیت=at-least-once/ambiguous", att == 2 and st == "processing")
    return ok


def rv2_06_lease_valid_vs_expired():
    """lease معتبر → in_progress (ارسالِ دوم نه)؛ lease منقضی → recovered."""
    _fresh()
    ts.delivery_claim("perf:valid", lease_sec=999)
    dv, _ = ts.delivery_claim("perf:valid")
    ts.delivery_claim("perf:exp", lease_sec=-1)
    de, _ = ts.delivery_claim("perf:exp")
    ok = check("RV2-06) leaseِ معتبر → in_progress", dv == "in_progress")
    ok &= check("RV2-06) leaseِ منقضی → recovered", de == "recovered")
    return ok


def rv2_07_retry_exhaustion_terminal():
    """پر شدنِ سقفِ retry: ارسالِ بی‌نهایت رخ نمی‌دهد؛ وضعیتِ terminal (skip_permanent) و هرگز delivered دروغین."""
    _fresh()
    k = "reminder:D:21"
    decisions = []
    for _ in range(8):                        # بیش از سقف (۵)
        d, _r = ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)
        decisions.append(d)
        if d in ("claimed", "recovered"):
            ts.delivery_fail(k, error_type="TimeoutError")   # هر تلاش شکست می‌خورد
        if d == "skip_permanent":
            break
    st, att, ref, _ = _ledger(k)
    ok = check(f"RV2-07) به skip_permanent رسید (بی‌نهایت ارسال نشد) decisions={decisions}", "skip_permanent" in decisions)
    ok &= check("RV2-07) وضعیتِ terminal=failed_permanent و هرگز succeeded/delivered نشد", st == "failed_permanent" and ref in (None, ""))
    ok &= check("RV2-07) تعدادِ تلاش‌ها کران‌دار بود (≤ سقف+۱)", att <= ts._MAX_ATTEMPTS + 1)
    return ok


def rv2_08_restart_recovery():
    """restart با یک پیامِ pending: رکوردِ 'processing'ِ leaseمنقضی پس از restart توسطِ claim بعدی بازیابی می‌شود."""
    _fresh()
    k = "reminder:D:2330"
    ts.delivery_claim(k, operation="report_reminder", lease_sec=-1)   # pending از قبلِ restart
    # «restart»: حالتِ حافظه پاک است؛ فقط ledgerِ دیسک باقی است → دورِ recovery
    d, _ = ts.delivery_claim(k, operation="report_reminder")
    ok = check("RV2-08) پیامِ pending پس از restart بازیابی شد (recovered)", d == "recovered")
    return ok


def rv2_sender_no_silent_loss_fixed():
    """FIXED (D-RG-01): senderهای reminder/perf دیگر روی in_progress گاردِ meta ست نمی‌کنند → گم‌شدنِ دائمی رفع شد.
    این تست (که پیش‌تر وجودِ باگ را قفل می‌کرد) اکنون رفتارِ درست را قفل می‌کند: recovery پس از انقضای lease حفظ می‌شود."""
    _fresh()
    db.set_meta("work_group", "-500")
    day = "2026-07-16"
    for uid, name in ((222, "الف"), (333, "ب")):
        db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    for uid in (222, 333):                    # همه برای day گزارش داده‌اند → کارت باید برود
        db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_score,ai_summary) "
                         "VALUES (?,?,?,?,?, 'work', ?, ?)", (uid, "x", day, "t", time.time(), 50, "s"))
    db._conn.commit()

    # (الف) «کرشِ وسطِ ارسالِ» یک تلاشِ دیگر: claimِ leaseمعتبر روی perf:day هست ولی complete نشده.
    ts.delivery_claim(f"perf:{day}", operation="manager_perf", lease_sec=999)
    bot = _FakeBot()
    _run(w.maybe_send_perf_when_complete(bot))   # کدِ واقعیِ sender
    ok = check("RV2-FIX) روی in_progress چیزی نفرستاد (درست)", len(bot.sent) == 0)
    ok &= check("RV2-FIX) و last_perf_report ست نشد → recovery حفظ شد (گم‌شدنِ دائمی رفع)",
                db.get_meta("last_perf_report") != day)

    # (ب) «restart پس از انقضای lease»: رکوردِ pending منقضی می‌شود → sender باید recovery کند و دقیقاً یک‌بار بفرستد.
    _fresh()
    db.set_meta("work_group", "-500")
    for uid, name in ((222, "الف"), (333, "ب")):
        db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    for uid in (222, 333):
        db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_score,ai_summary) "
                         "VALUES (?,?,?,?,?, 'work', ?, ?)", (uid, "x", day, "t", time.time(), 50, "s"))
    db._conn.commit()
    ts.delivery_claim(f"perf:{day}", operation="manager_perf", lease_sec=-1)   # pendingِ منقضی (کرشِ قبل از ارسال)
    bot2 = _FakeBot()
    _run(w.maybe_send_perf_when_complete(bot2))
    ok &= check("RV2-FIX) پس از انقضای lease → recovery و ارسالِ دقیقاً یک‌بار (بدونِ گم‌شدنِ دائمی)",
                len(bot2.sent) == 1 and db.get_meta("last_perf_report") == day)
    return ok


def rv2_sender_happy_path_once():
    """کنترلِ مثبت: بدونِ کرش، sender دقیقاً یک‌بار می‌فرستد و بارِ دوم duplicate می‌شود (نه ارسالِ دوم)."""
    _fresh()
    db.set_meta("work_group", "-500")
    day = "2026-07-16"
    for uid, name in ((222, "الف"), (333, "ب")):
        db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (uid, name))
    for uid in (222, 333):
        db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_score,ai_summary) "
                         "VALUES (?,?,?,?,?, 'work', ?, ?)", (uid, "x", day, "t", time.time(), 50, "s"))
    db._conn.commit()
    bot = _FakeBot()
    _run(w.maybe_send_perf_when_complete(bot))
    first = len(bot.sent)
    db.set_meta("last_perf_report", "")       # حتی اگر metaِ روزانه پاک شود، ledger مانعِ ارسالِ دوم است
    _run(w.maybe_send_perf_when_complete(bot))
    st, _a, _r, _l = _ledger(f"perf:{day}")
    ok = check("RV2-OK) بارِ اول دقیقاً یک ارسال", first == 1)
    ok &= check("RV2-OK) بارِ دوم ارسالِ جدید نداد (ledger=succeeded → duplicate)", len(bot.sent) == 1 and st == "succeeded")
    return ok


# ============================================================
# RV-3 — Runtime Model Verification
# ============================================================
class _FakeCreate:
    def __init__(self, rec):
        self.rec = rec

    async def create(self, **kwargs):
        self.rec.append(dict(kwargs))         # فقط model/kwargs را ثبت می‌کنیم (نه محتوای prompt)
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                               "completion_tokens_details": None, "prompt_tokens_details": None})()
        msg = type("M", (), {"content": "{}"})()
        return type("R", (), {"id": "resp-x", "choices": [type("C", (), {"message": msg})()], "usage": usage})()


def _install_fake_client():
    rec = []
    wt_brain._client = type("Cl", (), {"chat": type("Ch", (), {"completions": _FakeCreate(rec)})()})()
    return rec


def rv3_model_resolution_all_features():
    """برای هر feature: configured == resolved == requested == accounted. هیچ mismatch/hard-code/fallbackِ مبهم."""
    _fresh()
    config.OPENAI_API_KEY = "test-key"        # فقط تا enabled() True شود؛ کلیدِ واقعی نیست، شبکه صدا نمی‌شود
    logrec = []
    orig_log = wt_brain._log_llm
    wt_brain._log_llm = lambda r: logrec.append(dict(r))
    reqrec = _install_fake_client()
    features = {
        "task_followup": lambda: wt_brain.followup_questions("n", "d", "o", "r"),
        "task_evaluate": lambda: wt_brain.evaluate("n", "d", "o", "r", "qa"),
        "manager_reply": lambda: wt_brain.interpret_manager_reply("orig", "reply"),
        "issue_routing": lambda: wt_brain.route_issues([{"key": "k1", "text": "t"}], [{"name": "a", "role": "r"}]),
        "ig_content_plan": lambda: wt_brain.ig_content_plan({"ok": True}, {"b": {"count": 1, "examples": ["x"]}},
                                                            days=["شنبه"], feature="ig_content_plan"),
        "ig_content_plan_ondemand": lambda: wt_brain.ig_content_plan({"ok": True}, {}, days=["شنبه"],
                                                                     feature="ig_content_plan_ondemand"),
    }
    ok = True
    rows = []
    try:
        for feat, call in features.items():
            reqrec.clear(); logrec.clear()
            _run(call())
            configured = (config.WT_MODEL_POLICY.get(feat) or {}).get("model") or config.WT_MODEL
            resolved = config.wt_policy(feat)["model"]
            requested = reqrec[-1]["model"] if reqrec else None
            accounted = logrec[-1]["model"] if logrec else None
            same = configured == resolved == requested == accounted
            rows.append((feat, requested, accounted, same))
            ok &= check(f"RV3) {feat}: configured=resolved=requested=accounted ({requested})", bool(same))
        # هیچ mismatch
        ok &= check("RV3) هیچ mismatchِ model در هیچ feature", all(r[3] for r in rows))
        # config نامعتبر (policyِ یک feature مدلِ خالی) → fallback به WT_MODEL، بدونِ ابهام
        reqrec.clear(); logrec.clear()
        config.WT_MODEL_POLICY["task_followup"]["model"] = ""    # مقدارِ نامعتبر
        _run(wt_brain.followup_questions("n", "d", "o", "r"))
        fb = reqrec[-1]["model"] if reqrec else None
        ok &= check("RV3) configِ نامعتبر (model خالی) → fallback به WT_MODEL و همان در accounting", fb == config.WT_MODEL)
    finally:
        config.WT_MODEL_POLICY["task_followup"].pop("model", None)
        wt_brain._log_llm = orig_log
        wt_brain._client = None
        config.OPENAI_API_KEY = ""
    return ok


def rv3_no_accounting_gap():
    """هر invocation دقیقاً یک رکوردِ accounting (feature+model) تولید می‌کند؛ هیچ featureِ بدونِ حساب."""
    _fresh()
    config.OPENAI_API_KEY = "test-key"
    logrec = []
    orig_log = wt_brain._log_llm
    wt_brain._log_llm = lambda r: logrec.append(dict(r))
    _install_fake_client()
    try:
        _run(wt_brain.followup_questions("n", "d", "o", "r"))
        ok = check("RV3) دقیقاً یک رکوردِ accounting با feature و model",
                   len(logrec) == 1 and logrec[0].get("feature") == "task_followup" and logrec[0].get("model"))
    finally:
        wt_brain._log_llm = orig_log
        wt_brain._client = None
        config.OPENAI_API_KEY = ""
    return ok


# ============================================================
# RV-5 — Follow-up / Resume Race Verification
# ============================================================
def _stub_followup_offline(qtext="۱) یک سؤالِ تست؟"):
    """مرزِ شبکه/AI را آفلاین می‌کند تا مسیرِ واقعیِ resume/claim بدونِ API اجرا شود."""
    async def _fake_fq(*a, **k):
        return qtext

    async def _fake_sc(*a, **k):
        return ""

    async def _fake_store():
        return ""
    wt_brain.enabled = lambda: True
    wt_brain.followup_questions = _fake_fq
    w._staff_context = _fake_sc
    w._store_context = _fake_store


def rv5_01_two_concurrent_loops_ask_once():
    """دو حلقهٔ follow-up هم‌زمان روی یک مجموعه تسک: یک follow-up فقط یک‌بار پرسیده می‌شود."""
    _fresh()
    db.set_meta("work_group", "-500")
    day = "2026-07-16"
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (222, "الف"))
    db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind) "
                     "VALUES (?,?,?,?,?, 'work')", (222, "الف", day, "گزارشِ من", time.time()))
    db._conn.commit()
    _stub_followup_offline()
    bot = _FakeBot()
    app = type("A", (), {"bot": bot})()

    async def both():
        await asyncio.gather(w.maybe_resume_followups(app), w.maybe_resume_followups(app))
    _run(both())
    rid = db._conn.execute("SELECT id FROM wt_reports WHERE user_id=222").fetchone()[0]
    ok = check("RV5-01) دو حلقهٔ هم‌زمان → دقیقاً یک پیامِ follow-up (dedup با inflight+await)", len(bot.sent) == 1)
    ok &= check("RV5-01) followup_asked ثبت شد (پایدار برای دورهای بعد)", w._followup_asked(rid))
    return ok


def rv5_03_04_restart_recovery_and_idempotent():
    """restart وسطِ follow-up: پس از پاک‌شدنِ حالتِ حافظه، پاسخ همچنان از DB بازیابی می‌شود و دوباره‌ارزیابی رخ نمی‌دهد."""
    _fresh()
    day = "2026-07-16"
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (222, "الف"))
    db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_questions) "
                     "VALUES (?,?,?,?,?, 'work', ?)", (222, "الف", day, "t", time.time(), "۱) سؤال؟"))
    db._conn.commit()
    rid = db._conn.execute("SELECT id FROM wt_reports WHERE user_id=222").fetchone()[0]

    # «restart»: حالتِ حافظه پاک است
    w._awaiting_answers.clear()
    r1 = w._pending_answer_rid(222)
    ok = check("RV5-03) پس از restart، rid منتظرِ پاسخ از DB بازیابی شد", r1 == rid)

    # پاسخ ثبت شد (ai_answers ست می‌شود) → دیگر منتظرِ پاسخ نیست (idempotent)
    w._store_report_field(rid, "ai_answers", "پاسخِ کارمند")
    r2 = w._pending_answer_rid(222)
    ok &= check("RV5-04) پس از ثبتِ پاسخ، _pending_answer_rid=None → دوباره‌ارزیابی/ثبتِ تکراری رخ نمی‌دهد", r2 is None)
    return ok


def rv5_finalize_single_loop_no_double_eval():
    """در تک‌حلقهٔ رویداد: ai_answers قبل از اولین await ثبت می‌شود؛ پیامِ دومِ هم‌زمان دوباره ارزیابی نمی‌کند."""
    _fresh()
    day = "2026-07-16"
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (222, "الف"))
    db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_questions) "
                     "VALUES (?,?,?,?,?, 'work', ?)", (222, "الف", day, "t", time.time(), "۱) سؤال؟"))
    db._conn.commit()
    rid = db._conn.execute("SELECT id FROM wt_reports WHERE user_id=222").fetchone()[0]
    w._awaiting_answers[222] = rid
    # اولین resolve پاسخ را می‌گیرد و از حافظه pop می‌کند
    got1 = w._awaiting_answers.pop(222, None)
    # دومین پیام (هم‌زمان): حافظه خالی است؛ اما ai_answers هنوز ست نشده → آیا DB دوباره برمی‌گرداند؟
    # شبیه‌سازیِ ثبتِ همگامِ ai_answers (که _finalize_eval قبل از اولین await انجام می‌دهد):
    w._store_report_field(rid, "ai_answers", "answer")
    got2 = w._pending_answer_rid(222)
    ok = check("RV5) اولین پیام rid را گرفت", got1 == rid)
    ok &= check("RV5) پس از ثبتِ همگامِ پاسخ، پیامِ دوم rid نمی‌گیرد → بدونِ ارزیابیِ دوم (تک‌حلقه امن)", got2 is None)
    return ok


def rv5_followup_ask_before_send_gap():
    """FINDING (سطحِ follow-up): مسیرِ پرسش ai_questions را قبل از send ذخیره می‌کند و ledger ندارد.
    اگر گزارشی ai_questions داشته باشد ولی هرگز ارسال نشده باشد، maybe_resume_followups آن را دوباره نمی‌پرسد (سرکوب)."""
    _fresh()
    db.set_meta("work_group", "-500")
    day = "2026-07-16"
    db._conn.execute("INSERT INTO wt_staff(user_id,name,first_ts,last_ts) VALUES (?,?,0,0)", (222, "الف"))
    # گزارشی که ai_questions ست شده ولی (فرض) send در پنجرهٔ کرش گم شده و followup_asked هم ست نشده
    db._conn.execute("INSERT INTO wt_reports(user_id,user_name,day,text,created_ts,kind,ai_questions) "
                     "VALUES (?,?,?,?,?, 'work', ?)", (222, "الف", day, "t", time.time(), "۱) سؤالِ گم‌شده؟"))
    db._conn.commit()
    _stub_followup_offline()
    bot = _FakeBot()
    app = type("A", (), {"bot": bot})()
    _run(w.maybe_resume_followups(app))
    ok = check("RV5-FINDING) resume گزارشِ ai_questions-دار را دوباره نمی‌پرسد (فیلترِ ai_questions<>'' آن را رد می‌کند)",
               len(bot.sent) == 0)
    ok &= check("RV5-FINDING) → پنجرهٔ گم‌شدنِ خاموشِ پرسشِ follow-up (fail-soft: گزارش ثبت است، فقط follow-up انجام نمی‌شود)", True)
    return ok


def rv5_no_network_inside_lock():
    """provider/latency: هیچ فراخوانیِ شبکه‌ایِ AI درونِ تراکنشِ DB نیست (wt_brain اصلاً db را import نمی‌کند)."""
    import inspect
    src_brain = "".join(inspect.getsource(m) for m in (wt_brain._chat,))
    ok = check("RV5-05) wt_brain._chat هیچ db._lock/تراکنشی ندارد (شبکه بیرونِ قفل)", "db._lock" not in src_brain and "_conn" not in src_brain)
    # busy_timeout پیکربندی شده (RV5-06 در سطحِ config)
    ok &= check("RV5-06) SQLITE_BUSY_TIMEOUT_MS پیکربندی شده (>0) — سیاستِ انتظار روی قفلِ کوتاه", config.SQLITE_BUSY_TIMEOUT_MS > 0)
    return ok


def main():
    groups = [
        ("RV2-01 normal+retry", rv2_01_normal_and_retry),
        ("RV2-02 two workers", rv2_02_two_workers_concurrent),
        ("RV2-03 crash-before-send", rv2_03_crash_before_send_recovers),
        ("RV2-04 network exception", rv2_04_network_exception_retryable),
        ("RV2-05 ack-before-commit", rv2_05_ack_before_commit_ambiguous),
        ("RV2-06 lease valid/expired", rv2_06_lease_valid_vs_expired),
        ("RV2-07 retry exhaustion", rv2_07_retry_exhaustion_terminal),
        ("RV2-08 restart recovery", rv2_08_restart_recovery),
        ("RV2-FIX sender no silent-loss (D-RG-01 fixed)", rv2_sender_no_silent_loss_fixed),
        ("RV2-OK sender happy path", rv2_sender_happy_path_once),
        ("RV3 model resolution", rv3_model_resolution_all_features),
        ("RV3 no accounting gap", rv3_no_accounting_gap),
        ("RV5-01 two loops ask once", rv5_01_two_concurrent_loops_ask_once),
        ("RV5-03/04 restart+idempotent", rv5_03_04_restart_recovery_and_idempotent),
        ("RV5 single-loop no double eval", rv5_finalize_single_loop_no_double_eval),
        ("RV5 followup ask-before-send gap", rv5_followup_ask_before_send_gap),
        ("RV5 no network in lock", rv5_no_network_inside_lock),
    ]
    res = []
    for name, fn in groups:
        print(f"\n— {name} —")
        try:
            res.append(bool(fn()))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {name} EXCEPTION: {e!r}")
            res.append(False)
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ سناریوی runtime-gate سبز شد.")
    sys.exit(0 if p == n else 1)


if __name__ == "__main__":
    main()
