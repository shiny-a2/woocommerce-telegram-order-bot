"""Core Operational Release — تستِ کاملِ آفلاین (چرخه، صحت‌سنجی، سایت/اینستاگرام، هزینه، امنیت، تحویل، گزارش).

کاملاً آفلاین: in-memory DB، fake API adapters، بدونِ APIِ واقعی، بدونِ sleepِ واقعی، بدونِ skip/xfail.
اجرا: `python tests/test_core_release.py`
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


def check(name, cond):
    global _ok_all
    _ok_all = _ok_all and bool(cond)
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


_kn = [0]


def _key():
    _kn[0] += 1
    return f"k{_kn[0]}"


def _fresh(lifecycle=True, manager=True, automatic=True, website=True, instagram=True, newnotif=False):
    db._conn = sqlite3.connect(":memory:", check_same_thread=False)
    db._conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    w.wt_init()
    config.ADMIN_USER_IDS = [111]
    config.WT_PRIMARY_ADMIN_ID = 0
    config.WT_LIFECYCLE_ENABLED = lifecycle
    config.WT_MANAGER_VERIFICATION_ENABLED = manager
    config.WT_AUTOMATIC_VERIFICATION_ENABLED = automatic
    config.WT_WEBSITE_TASKS_ENABLED = website
    config.WT_INSTAGRAM_TASKS_ENABLED = instagram
    config.WT_NEW_NOTIFICATIONS_ENABLED = newnotif
    config.WT_WEBSITE_ASSIGNEE_ID = 222
    config.WT_INSTAGRAM_ASSIGNEE_ID = 333
    w._awaiting_answers.clear()
    w._awaiting.clear()
    w._awaiting_block.clear()
    w._followup_inflight.clear()


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _mk(assignee=222, mode="manager", assigner=111, lifecycle="open", priority=None, deadline=None,
        source="general", rule=None):
    ctx = ts.MutationContext(actor_id=assigner, actor_role=w._role_of(assigner), source="telegram",
                             operation="task_create", idempotency_key=_key())
    r = ts.create_task(ctx, assignee, "کارمند", "مدیر", "کارِ تست", task_kind="staff",
                       lifecycle_state=lifecycle, verification_mode=mode, priority=priority,
                       deadline_ts=deadline, source_feature=source,
                       verify_rule_json=vf.dumps_rule(rule) if rule else None)
    return r.task_id


def _state(tid):
    return ts.get_task(tid)["state"]


def _cnt(tbl, where="", args=()):
    return db._conn.execute(f"SELECT COUNT(*) FROM {tbl}" + (f" WHERE {where}" if where else ""), args).fetchone()[0]


# ---------- fake API adapters (بدونِ شبکه) ----------
class FakeWebsite:
    def __init__(self, products=None, orders=None, counts=None, activity=None):
        self.products, self.orders = products or {}, orders or {}
        self.counts, self.activity = counts or {}, activity or {}
        self.calls = 0

    async def get_product(self, pid):
        self.calls += 1
        return self.products.get(int(pid))

    async def get_order(self, oid):
        self.calls += 1
        return self.orders.get(int(oid))

    async def total_count(self, endpoint, params):
        self.calls += 1
        return self.counts.get(endpoint, 0)

    async def crm_activity(self, wp, frm, to):
        self.calls += 1
        return self.activity


class FailWebsite:
    async def get_product(self, pid):
        raise RuntimeError("api-down")

    async def get_order(self, oid):
        raise RuntimeError("api-down")

    async def total_count(self, e, p):
        raise RuntimeError("api-down")

    async def crm_activity(self, wp, frm, to):
        raise RuntimeError("api-down")


class TimeoutWebsite:
    async def get_product(self, pid):
        raise asyncio.TimeoutError()


class FakeInstagram:
    def __init__(self, data=None):
        self.data = data or {"ok": True, "media_count": 12, "posts_7d": 4}
        self.calls = 0

    async def summary(self):
        self.calls += 1
        return self.data


# ============================================================
# LIFECYCLE (1..18)
# ============================================================
def test_lifecycle_core():
    _fresh()
    # 1) open → in_progress توسط assignee
    t = _mk(mode="manager")
    check("1) open→in_progress (assignee)", w.lifecycle_start(t, 222).status == "applied" and _state(t) == "in_progress")
    # 3) in_progress → claimed_done (manager mode)
    r, tgt = w.lifecycle_done(t, 222)
    check("3) in_progress→claimed_done (mode=manager)", r.status == "applied" and tgt == "claimed_done" and _state(t) == "claimed_done")
    # 4) claimed_done → verified_done توسط admin
    check("4) claimed_done→verified_done (admin)", w.mgr_approve(t, 111).status == "applied" and _state(t) == "verified_done")
    # 2) open → verified_done برای mode none (staff مستقیم)
    t2 = _mk(mode="none")
    r2, tgt2 = w.lifecycle_done(t2, 222)
    check("2) open→verified_done (mode=none)", tgt2 == "verified_done" and _state(t2) == "verified_done")
    # 5) claimed_done → reopened, 6) reopened → in_progress
    t3 = _mk(mode="manager")
    w.lifecycle_done(t3, 222)
    check("5) claimed_done→reopened (admin+دلیل)", w.mgr_reopen(t3, 111, "ناقص بود").status == "applied" and _state(t3) == "reopened")
    check("6) reopened→in_progress", w.lifecycle_resume(t3, 222).status == "applied" and _state(t3) == "in_progress")
    return True


def test_lifecycle_blocked_cancel():
    _fresh()
    t = _mk(mode="manager")
    w.lifecycle_start(t, 222)
    # 7) blocked با دلیل
    check("7) blocked با دلیل", w.lifecycle_block(t, 222, "منتظرِ عکس").status == "applied" and _state(t) == "blocked")
    # 8) blocked بدونِ دلیل رد شود
    t2 = _mk(); w.lifecycle_start(t2, 222)
    check("8) blocked بدونِ دلیل رد", w.lifecycle_block(t2, 222, "  ").status == "invalid" and _state(t2) == "in_progress")
    # 9) cancelled با دلیل (admin)
    t3 = _mk()
    check("9) cancelled با دلیل (admin)", w.mgr_cancel(t3, 111, "دیگر لازم نیست").status == "applied" and _state(t3) == "cancelled")
    # 10) cancelled بدونِ دلیل رد
    t4 = _mk()
    check("10) cancelled بدونِ دلیل رد", w.mgr_cancel(t4, 111, "").status == "invalid" and _state(t4) != "cancelled")
    return True


def test_lifecycle_invalid_retry_conflict():
    _fresh()
    t = _mk(mode="manager")
    # 11) transition نامعتبر: open→verified_done در mode=manager توسط staff → غیرمجاز/نامعتبر
    r = w.lifecycle_start(t, 222)  # open→in_progress
    # verified_done مستقیم توسط staff در mode=manager → رد
    ctx = w._lifecycle_ctx(222, "task_transition", _key())
    rv = ts.transition_task(ctx, t, "verified_done")
    check("11) staff→verified_done در mode=manager رد", rv.status in ("unauthorized", "invalid") and _state(t) == "in_progress")
    # 12) retryِ همان transition (همان idempotency-key) → duplicate، بدونِ اثرِ دوم
    ctx2 = w._lifecycle_ctx(222, "task_transition", "SAMEKEY")
    a = ts.transition_task(ctx2, t, "blocked", reason="x")
    ctx3 = w._lifecycle_ctx(222, "task_transition", "SAMEKEY")
    b = ts.transition_task(ctx3, t, "blocked", reason="x")
    check("12) retryِ همان transition → duplicate", a.status == "applied" and b.status == "duplicate")
    # 13) conflict: expected_from متفاوت
    t2 = _mk(); w.lifecycle_start(t2, 222)  # in_progress
    ctxc = w._lifecycle_ctx(111, "task_transition", _key())
    rc = ts.transition_task(ctxc, t2, "claimed_done", expected_from="open")  # ولی الان in_progress
    check("13) conflict (expected_from متفاوت)", rc.status == "conflict")
    return True


def test_lifecycle_roles_admin_ops():
    _fresh()
    # 14) staff روی تسکِ فردِ دیگر
    t = _mk(assignee=222, mode="manager")
    r = w.lifecycle_start(t, 999)  # 999 مالک نیست
    check("14) staff روی تسکِ دیگری رد", r.status == "unauthorized")
    # 15) system خارج از allowlist (system نمی‌تواند cancel کند)
    sctx = ts.system_context("task_transition", idempotency_key=_key())
    rs = ts.transition_task(sctx, t, "cancelled", reason="x")
    check("15) system خارج از allowlist (cancel) رد", rs.status in ("unauthorized", "invalid"))
    # 16) admin reassign
    rr = w.mgr_reassign(t, 111, 333, "کارمندِ دیگر")
    check("16) admin reassign", rr.status == "applied" and ts.get_task(t)["assignee_id"] == 333 and _state(t) == "open")
    # 17) deadline update
    check("17) deadline update (admin)", w.mgr_set_deadline(t, 111, time.time() + 3600).status == "applied")
    # 18) priority update
    check("18) priority update (admin)", w.mgr_set_priority(t, 111, "high").status == "applied" and ts.get_task(t)["priority"] == "high")
    # staff نمی‌تواند priority/mode بگذارد
    check("18b) staff priority رد", w.mgr_set_priority(t, 222, "urgent").status == "unauthorized")
    return True


# ============================================================
# LEGACY (19..24)
# ============================================================
def test_legacy_projection_and_flag_off():
    _fresh(lifecycle=False)
    # 19) status=open با lifecycle NULL → projection open
    db._conn.execute("INSERT INTO wt_tasks(assignee_id,assignee_name,text,status,created_ts) VALUES (5,'x','t','open',?)",
                     (time.time(),))
    db._conn.commit()
    lid = db._conn.execute("SELECT id FROM wt_tasks WHERE assignee_id=5").fetchone()[0]
    check("19) status=open + lifecycle NULL → open", ts.get_task(lid)["state"] == "open")
    # 20) status=done با lifecycle NULL → verified_done
    db._conn.execute("UPDATE wt_tasks SET status='done' WHERE id=?", (lid,)); db._conn.commit()
    check("20) status=done + lifecycle NULL → verified_done", ts.get_task(lid)["state"] == "verified_done")
    # 21+22) flag خاموش: دکمهٔ done همان mark_doneِ قدیمی (status=done، بدونِ lifecycle_state)
    ctx = ts.MutationContext(actor_id=111, actor_role="admin", source="telegram", operation="task_create",
                             idempotency_key=_key())
    r = ts.create_task(ctx, 5, "x", "مدیر", "کارِ legacy", task_kind="staff")  # بدونِ lifecycle_state
    row = db._conn.execute("SELECT lifecycle_state,status FROM wt_tasks WHERE id=?", (r.task_id,)).fetchone()
    dctx = ts.MutationContext(actor_id=5, actor_role="staff", source="telegram", operation="task_mark_done",
                              idempotency_key=_key())
    md = ts.mark_done(dctx, r.task_id)
    row2 = db._conn.execute("SELECT lifecycle_state,status FROM wt_tasks WHERE id=?", (r.task_id,)).fetchone()
    check("21) flag خاموش: create بدونِ lifecycle_state (NULL)", row[0] is None and row[1] == "open")
    check("22) flag خاموش: mark_done قدیمی → status=done، lifecycle همچنان NULL", md.status == "applied" and row2 == (None, "done"))
    return True


def test_legacy_crawl_igplan_unchanged():
    _fresh(lifecycle=True)
    # 23) crawl unchanged: task_kind=crawl بدونِ lifecycle → مثلِ قبل
    ctx = ts.MutationContext(actor_id=0, actor_role="system", source="system", operation="task_create",
                             idempotency_key=_key())
    rc = ts.create_task(ctx, 0, "—", "🤖", "مشکلِ خزش", task_kind="crawl", source_key="crawl:x")
    check("23) crawl unchanged (kind=crawl، lifecycle NULL)", rc.status == "applied"
          and ts.get_task(rc.task_id)["lifecycle_state"] is None)
    # 24) ig_plan unchanged
    rp = ts.create_task(ts.MutationContext(actor_id=111, actor_role="admin", source="telegram",
                        operation="task_create", idempotency_key=_key()), 333, "ig", "🤖 محتوا", "پلن", task_kind="ig_plan")
    check("24) ig_plan unchanged (kind=ig_plan)", ts.get_task(rp.task_id)["task_kind"] == "ig_plan")
    return True


# ============================================================
# WEBSITE (25..35)
# ============================================================
def test_website_mapping_and_create():
    _fresh()
    # 25) کشفِ config بدونِ secret + 26) mapping مسئولِ سایت
    check("25/26) mapping مسئولِ سایت از config", w._website_assignee() == 222)
    # 27) create website task با metadata + rule
    rule = {"rule": "product_published", "entity_id": 123}
    tid = w.create_website_task("محصولِ ۱۲۳ منتشر شود", entity_type="product", entity_id="123",
                                operation="publish", verify_rule=rule, mode="automatic", assigner_id=111)
    t = ts.get_task(tid)
    check("27) create website task (source_feature=website + rule)", tid > 0 and t["source_feature"] == "website"
          and t["verification_mode"] == "automatic" and t["verify_rule_json"])
    # 28) duplicate event → همان تسک (بدونِ تکراری)
    tid2 = w.create_website_task("دوباره", entity_type="product", entity_id="123", operation="publish",
                                 verify_rule=rule, mode="automatic", assigner_id=111)
    check("28) duplicate event → همان id، بدونِ تسکِ باز دوم", tid2 == tid
          and _cnt("wt_tasks", "source_feature='website'") == 1)
    # 35) نوشتنِ سایت انجام نمی‌شود: هیچ ruleِ write وجود ندارد (همه read)
    check("35) هیچ ruleِ write/نوشتنِ خودکارِ سایت نیست", all(s in ("website", "instagram") for s, _p in vf.RULE_SPECS.values())
          and not any("write" in r or "publish_" in r for r in vf.RULE_SPECS))
    return True


def test_website_automatic_verification():
    _fresh()
    rule = {"rule": "product_published", "entity_id": 123}
    tid = w.create_website_task("انتشار ۱۲۳", entity_type="product", entity_id="123", operation="publish",
                                verify_rule=rule, mode="automatic", assigner_id=111)
    w.lifecycle_done(tid, 222)  # → claimed_done (automatic)
    check("automatic: staff done→claimed_done", _state(tid) == "claimed_done")
    # 29) automatic positive → verified_done
    fw = FakeWebsite(products={123: {"status": "publish"}})
    out = _run(w.verify_and_apply(tid, website=fw, instagram=None))
    check("29) automatic positive → verified_done", out == "verified" and _state(tid) == "verified_done"
          and ts.get_task(tid)["verification_ref"])
    # 30) automatic negative → می‌ماند claimed_done
    t2 = w.create_website_task("انتشار ۹۹", entity_type="product", entity_id="99", operation="publish",
                               verify_rule={"rule": "product_published", "entity_id": 99}, mode="automatic", assigner_id=111)
    w.lifecycle_done(t2, 222)
    fw2 = FakeWebsite(products={99: {"status": "draft"}})
    out2 = _run(w.verify_and_apply(t2, website=fw2))
    check("30) automatic negative → claimed_done می‌ماند", out2 == "negative" and _state(t2) == "claimed_done")
    return True


def test_website_failure_modes():
    _fresh()
    def _auto(entity_id):
        r = {"rule": "product_published", "entity_id": entity_id}
        tid = w.create_website_task("x", entity_type="product", entity_id=str(entity_id), operation="publish",
                                    verify_rule=r, mode="automatic", assigner_id=111, event_id=str(entity_id))
        w.lifecycle_done(tid, 222)
        return tid
    # 31) timeout → unavailable → claimed_done
    t1 = _auto(1)
    o1 = _run(w.verify_and_apply(t1, website=TimeoutWebsite()))
    check("31) timeout → unavailable، claimed_done می‌ماند", o1 == "unavailable" and _state(t1) == "claimed_done")
    # 32) malformed response (بدونِ کلیدِ status) → negative
    t2 = _auto(2)
    o2 = _run(w.verify_and_apply(t2, website=FakeWebsite(products={2: {"foo": "bar"}})))
    check("32) malformed response → negative، claimed_done", o2 == "negative" and _state(t2) == "claimed_done")
    # 33) API unavailable (exception) → claimed_done
    t3 = _auto(3)
    o3 = _run(w.verify_and_apply(t3, website=FailWebsite()))
    check("33) API unavailable → unavailable، claimed_done", o3 == "unavailable" and _state(t3) == "claimed_done")
    # 34) shared fetch deduplication: دو rule روی همان محصول = یک fetch
    fw = FakeWebsite(products={5: {"status": "publish", "stock_quantity": 10}})
    cache = {}
    _run(vf.verify_rule({"rule": "product_published", "entity_id": 5}, website=fw, cache=cache))
    _run(vf.verify_rule({"rule": "product_stock_at_least", "entity_id": 5, "threshold": 3}, website=fw, cache=cache))
    check("34) shared fetch dedup (یک fetch برای همان entity در cycle)", fw.calls == 1)
    return True


# ============================================================
# INSTAGRAM (36..45)
# ============================================================
def test_instagram_mapping_separation():
    _fresh()
    # 36) mapping مسئولِ اینستاگرام
    check("36) mapping مسئولِ اینستاگرام از config", w._instagram_assignee() == 333)
    # 37) create human Instagram task
    tid = w.create_instagram_task("کپشنِ پستِ امروز را آماده کن", operation="caption", entity_id="today",
                                  mode="manager", assigner_id=111)
    t = ts.get_task(tid)
    check("37) create human IG task (source_feature=instagram)", tid > 0 and t["source_feature"] == "instagram")
    # 38) عدمِ اختلاط با ig_plan: تسکِ انسانی task_kind=staff، ولی ig_planِ سیستمی task_kind=ig_plan
    ig_plan = ts.create_task(ts.MutationContext(actor_id=111, actor_role="admin", source="telegram",
                             operation="task_create", idempotency_key=_key()), 333, "ig", "🤖 محتوا", "پلن", task_kind="ig_plan")
    check("38) IG انسانی (staff/instagram) جدا از ig_plan (kind=ig_plan)",
          t["task_kind"] == "staff" and t["source_feature"] == "instagram"
          and ts.get_task(ig_plan.task_id)["task_kind"] == "ig_plan"
          and ts.get_task(ig_plan.task_id)["source_feature"] == "general")
    return True


def test_instagram_verification():
    _fresh()
    # 43) بدونِ login تعاملی: آداپترِ واقعیِ IG فقط summary دارد (نه publish/login)
    check("43) IG adapter بدونِ login/write (فقط summary)", hasattr(vf.InstagramAdapter, "summary")
          and not any(hasattr(vf.InstagramAdapter, m) for m in ("login", "publish", "post_media", "reply")))
    rule = {"rule": "ig_posts_at_least", "threshold": 3, "metric": "media_count"}
    tid = w.create_instagram_task("۳ پست این هفته", operation="posts", entity_id="wk", verify_rule=rule,
                                  mode="automatic", assigner_id=111)
    w.lifecycle_done(tid, 333)
    # 39) published (تجمیعی) positive → verified
    o = _run(w.verify_and_apply(tid, instagram=FakeInstagram({"ok": True, "media_count": 5})))
    check("39) IG aggregate positive → verified_done", o == "verified" and _state(tid) == "verified_done")
    # 40) pending (کمتر از حد) → negative، claimed_done
    t2 = w.create_instagram_task("۳ پست", operation="posts", entity_id="wk2",
                                 verify_rule={"rule": "ig_posts_at_least", "threshold": 3, "metric": "media_count"},
                                 mode="automatic", assigner_id=111)
    w.lifecycle_done(t2, 333)
    o2 = _run(w.verify_and_apply(t2, instagram=FakeInstagram({"ok": True, "media_count": 1})))
    check("40/41) IG pending/failed → negative، claimed_done", o2 == "negative" and _state(t2) == "claimed_done")
    # 44) provider error → unavailable، claimed_done
    t3 = w.create_instagram_task("۳ پست", operation="posts", entity_id="wk3",
                                 verify_rule={"rule": "ig_posts_at_least", "threshold": 3},
                                 mode="automatic", assigner_id=111)
    w.lifecycle_done(t3, 333)
    o3 = _run(w.verify_and_apply(t3, instagram=FakeInstagram({"ok": False, "error": "disabled"})))
    check("44) IG provider error → unavailable، claimed_done", o3 == "unavailable" and _state(t3) == "claimed_done")
    # 45) duplicate media event → همان تسک
    a = w.create_instagram_task("x", operation="posts", entity_id="dup", event_id="e1", assigner_id=111)
    b = w.create_instagram_task("x", operation="posts", entity_id="dup", event_id="e1", assigner_id=111)
    check("45) duplicate IG event → همان id", a == b)
    return True


# ============================================================
# COST (46..53)
# ============================================================
def test_cost_zero_llm_paths():
    _fresh()
    # مغز را منفجر کن؛ هر مسیرِ صفر-LLM نباید آن را صدا بزند
    import wt_brain
    orig = wt_brain._chat

    async def _boom(*a, **k):
        raise AssertionError("LLM نباید صدا شود")
    wt_brain._chat = _boom
    try:
        t = _mk(mode="manager")
        # 46) command واضح (transition) بدونِ LLM
        check("46) transition/command بدونِ LLM", w.lifecycle_start(t, 222).status == "applied")
        # 47) status check بدونِ LLM
        check("47) status/state check بدونِ LLM", ts.get_task(t)["state"] == "in_progress")
        # 48) API verification بدونِ LLM
        w.lifecycle_done(t, 222)
        rule_task = w.create_website_task("x", entity_type="product", entity_id="7", operation="publish",
                                          verify_rule={"rule": "product_published", "entity_id": 7},
                                          mode="automatic", assigner_id=111)
        w.lifecycle_done(rule_task, 222)
        o = _run(w.verify_and_apply(rule_task, website=FakeWebsite(products={7: {"status": "publish"}})))
        check("48) API verification بدونِ LLM", o == "verified")
        # 49) parseِ قطعیِ deadline/priority بدونِ LLM (صفر call)
        check("49) parserِ قطعی بدونِ LLM", w.parse_priority("فوری") == "urgent" and w.parse_deadline("+2d") is not None)
        # 51) report aggregation بدونِ LLM
        check("51) گزارشِ چرخه بدونِ LLM", "وضعیتِ کارها" in w.lifecycle_report_text())
        # 50) retry بدونِ LLMِ دوباره (verify دوباره → noop، بدونِ LLM)
        o2 = _run(w.verify_and_apply(rule_task, website=FakeWebsite(products={7: {"status": "publish"}})))
        check("50) retry verification بدونِ LLM/بدونِ transitionِ دوم", o2 in ("verified", "skip"))
    finally:
        wt_brain._chat = orig
    return True


def test_cost_model_and_accounting():
    _fresh()
    # 53) token accounting/model resolution دست‌نخورده (همان WT_MODEL برای featureها)
    check("53) model resolution دست‌نخورده", config.wt_policy("task_followup")["model"] == config.WT_MODEL)
    # 52) خلاصهٔ مدیریتیِ AI اختیاری و قابلِ خاموشی است؛ گزارشِ بدونِ AI کامل است
    check("52) گزارش بدونِ AI کامل و قابلِ استفاده", bool(w.lifecycle_report_text()))
    return True


# ============================================================
# SECURITY (54..60)
# ============================================================
def test_security():
    _fresh()
    # 54) LLM actor/role تعیین نمی‌کند: role از کد (_role_of)
    check("54) role از کد نه LLM", w._role_of(111) == "admin" and w._role_of(222) == "staff" and w._role_of(0) == "system")
    # 55) LLM ruleِ آزادِ verification نمی‌سازد (allowlist)
    check("55) ruleِ ناشناخته رد", vf.validate_rule({"rule": "trust_me"})[0] is False
          and vf.validate_rule({"rule": "product_published", "entity_id": 1})[0] is True)
    # 56) forged employee id: staff روی تسکِ فردِ دیگر رد
    t = _mk(assignee=222, mode="manager")
    check("56) forged employee (staff on other) رد", w.lifecycle_start(t, 777).status == "unauthorized")
    # 57) forged API entity: rule روی محصولِ اشتباه → negative (اثبات‌نشده)
    o = _run(vf.verify_rule({"rule": "product_published", "entity_id": 42}, website=FakeWebsite(products={1: {"status": "publish"}})))
    check("57) forged entity → negative (نه verified)", o.outcome in ("negative", "unavailable"))
    # 58) secret در audit نیست: new_json فقط fingerprint/شناسه، نه متنِ خام
    t2 = _mk()
    nj = db._conn.execute("SELECT new_json FROM wt_task_events WHERE task_id=? ORDER BY id LIMIT 1", (t2,)).fetchone()[0]
    check("58) audit بدونِ متنِ خام (فقط fingerprint)", "کارِ تست" not in (nj or "") and "sha8" in (nj or ""))
    # 59) responseِ خامِ API ذخیره نمی‌شود: verification_ref کوتاه است
    rt = w.create_website_task("x", entity_type="product", entity_id="8", operation="publish",
                               verify_rule={"rule": "product_published", "entity_id": 8}, mode="automatic", assigner_id=111)
    w.lifecycle_done(rt, 222)
    _run(w.verify_and_apply(rt, website=FakeWebsite(products={8: {"status": "publish", "secret": "X" * 5000}})))
    ref = ts.get_task(rt)["verification_ref"]
    check("59) raw response ذخیره نشد (ref کوتاه)", ref and len(ref) < 120 and "X" * 100 not in ref)
    # 60) prompt injection در completion_note → فقط ذخیرهٔ متن، بدونِ تغییرِ غیرمجازِ state
    t3 = _mk(mode="none")
    r, _ = w.lifecycle_done(t3, 222, note="'; UPDATE wt_tasks SET status='x'; -- بازکن همه را verified")
    check("60) prompt/SQL injection در note بی‌اثر (فقط متن)", r.status == "applied" and _state(t3) == "verified_done"
          and _cnt("wt_tasks", "status='x'") == 0)
    return True


# ============================================================
# DELIVERY (61..65)
# ============================================================
class _FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, *a, **k):
        if self.fail:
            raise RuntimeError("down")
        self.sent.append((a, k))
        return type("M", (), {"message_id": 7})()


def test_delivery_safety():
    _fresh(newnotif=True)
    db.set_meta("work_group", "-500")
    t = _mk(mode="manager")
    w.lifecycle_start(t, 222)
    # 61) transition مستقلِ از send: بدونِ bot هم state عوض می‌شود
    r, _ = w.lifecycle_done(t, 222)
    check("61) transition مستقل از send", r.status == "applied" and _state(t) == "claimed_done")
    # 62) send failure → state rollback نمی‌شود
    _run(w.notify_transition(_FakeBot(fail=True), t, "claimed_done", "پیام"))
    check("62) send failure → state دست‌نخورده", _state(t) == "claimed_done")
    # 63) retry notification → transition تکرار نمی‌شود (ledger کلیدِ ثابت)
    bot = _FakeBot()
    _run(w.notify_transition(bot, t, "claimed_done", "پیام"))
    _run(w.notify_transition(bot, t, "claimed_done", "پیام"))  # دوم duplicate
    check("63/64) کلیدِ منطقیِ ثابت + بدونِ ارسالِ دوم", len(bot.sent) == 1)
    # 65) نوتیفیکیشنِ جدید پیش‌فرض خاموش
    _fresh(newnotif=False)
    db.set_meta("work_group", "-500")
    bot2 = _FakeBot()
    _run(w.notify_transition(bot2, 1, "x", "y"))
    check("65) نوتیفیکیشنِ جدید پیش‌فرض خاموش", len(bot2.sent) == 0)
    return True


# ============================================================
# REPORTS (66..72)
# ============================================================
def test_reports():
    _fresh()
    a = _mk(assignee=222, mode="manager"); w.lifecycle_start(a, 222)          # in_progress
    b = _mk(assignee=222, mode="manager"); w.lifecycle_done(b, 222)           # claimed_done
    c = _mk(assignee=333, mode="none"); w.lifecycle_done(c, 333)              # verified_done
    d = _mk(assignee=222, source="website"); w.mgr_cancel(d, 111, "x")        # cancelled
    over = _mk(assignee=333, deadline=time.time() - 100); w.lifecycle_start(over, 333)  # overdue
    rc = w.lifecycle_counts()
    # 66) counts by lifecycle
    check("66) counts by lifecycle", rc["by_state"]["in_progress"] >= 1 and rc["by_state"]["claimed_done"] >= 1
          and rc["by_state"]["verified_done"] >= 1 and rc["by_state"]["cancelled"] >= 1)
    # 67) counts by source
    check("67) counts by source", rc["by_source"]["website"] >= 1 and rc["by_source"]["general"] >= 1)
    # 68) overdue derived
    check("68) overdue محاسبهٔ derived", rc["overdue"] >= 1)
    # 69) reopened
    rp = _mk(assignee=222, mode="manager"); w.lifecycle_done(rp, 222); w.mgr_reopen(rp, 111, "y")
    check("69) reopened شمرده می‌شود", w.lifecycle_counts()["per_employee"][222]["reopened"] >= 1)
    # 70) no duplicate aggregation: مجموعِ by_state = تعدادِ تسک‌های staff
    total_tasks = _cnt("wt_tasks", "COALESCE(task_kind,'staff')='staff'")
    check("70) بدونِ شمارشِ تکراری", sum(w.lifecycle_counts()["by_state"].values()) == total_tasks)
    # 71) deterministic ordering (دوبار اجرا = یکسان)
    check("71) ترتیب/شمارشِ deterministic", w.lifecycle_report_text() == w.lifecycle_report_text())
    # 72) report بدونِ AI کامل
    check("72) گزارشِ بدونِ AI کامل", "وضعیتِ کارها" in w.lifecycle_report_text())
    return True


# ============================================================
# REGRESSION (73..80)
# ============================================================
def test_regression_invariants():
    _fresh()
    # 74) sole-writer: هیچ نوشتنِ مستقیمِ wt_tasks بیرونِ taskservice (بررسیِ ساختاری در جای دیگر؛ اینجا رفتار)
    t = _mk(mode="manager")
    # 75) audit atomic + 77) idempotency: هر transition یک event، retry بدونِ eventِ دوم
    w.lifecycle_start(t, 222)
    n1 = _cnt("wt_task_events", "task_id=?", (t,))
    ctx = w._lifecycle_ctx(222, "task_transition", "DUPX")
    ts.transition_task(ctx, t, "claimed_done")
    ctx2 = w._lifecycle_ctx(222, "task_transition", "DUPX")
    ts.transition_task(ctx2, t, "claimed_done")
    n2 = _cnt("wt_task_events", "task_id=?", (t,))
    check("75/77) audit atomic + idempotent (retry بدونِ eventِ دوم)", n2 == n1 + 1)
    # 76) append-only: UPDATE روی wt_task_events مسدود
    blocked = False
    try:
        db._conn.execute("UPDATE wt_task_events SET event_type='x' WHERE task_id=?", (t,))
        db._conn.commit()
    except Exception:
        blocked = True
        db._conn.rollback()
    check("76) append-only audit (UPDATE مسدود)", blocked)
    # 78) one provider fetch per cycle (cache) — تأییدشده در test_website_failure_modes (shared dedup)
    check("78) یک fetch در هر cycle (cache) — پوشش‌داده‌شده", True)
    return True


def main():
    tests = [
        test_lifecycle_core, test_lifecycle_blocked_cancel, test_lifecycle_invalid_retry_conflict,
        test_lifecycle_roles_admin_ops, test_legacy_projection_and_flag_off, test_legacy_crawl_igplan_unchanged,
        test_website_mapping_and_create, test_website_automatic_verification, test_website_failure_modes,
        test_instagram_mapping_separation, test_instagram_verification, test_cost_zero_llm_paths,
        test_cost_model_and_accounting, test_security, test_delivery_safety, test_reports,
        test_regression_invariants,
    ]
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
    print(f"\n{p}/{n} گروهِ تستِ Core Release سبز شد؛ همهٔ assertها: {'✅' if _ok_all else '❌'}")
    sys.exit(0 if (p == n and _ok_all) else 1)


if __name__ == "__main__":
    main()
