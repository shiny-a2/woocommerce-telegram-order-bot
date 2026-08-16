"""تستِ فاز صفر: لایهٔ مرکزیِ LLM (usage/latency)، policyِ feature، snapshotِ context، routingِ قطعی.

کاملاً آفلاین — هیچ network/OpenAI/Telegram واقعی صدا زده نمی‌شود (fake client تزریق می‌شود).
اجرا: `python tests/test_wt_llm.py`  (بدونِ نیاز به pytest).
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import wt_brain        # noqa: E402


def check(name, cond):
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- fakeهای OpenAI (بدونِ شبکه) ----------
class _Attr:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content, usage=None, rid="resp_test"):
        self.choices = [_Choice(content)]
        self.usage = usage
        self.id = rid


class _Completions:
    def __init__(self, outer):
        self.outer = outer

    async def create(self, **kwargs):
        self.outer.calls.append(kwargs)
        if self.outer.raise_exc:
            raise self.outer.raise_exc
        return self.outer.resp


class FakeClient:
    def __init__(self, resp=None, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.calls = []
        self.chat = _Attr(completions=_Completions(self))


def _install(resp=None, raise_exc=None):
    """fake client + کلیدِ تستی + گیرندهٔ لاگ. خروجی: (client, logs)."""
    config.OPENAI_API_KEY = "test-key"            # نه واقعی؛ فقط برای enabled()
    fc = FakeClient(resp=resp, raise_exc=raise_exc)
    wt_brain._client = fc
    logs = []
    wt_brain._log_llm = lambda rec: logs.append(rec)   # noqa: E731 — گرفتنِ متریک به‌جای چاپ
    return fc, logs


def _usage_full():
    return _Attr(prompt_tokens=120, completion_tokens=40, total_tokens=160,
                 completion_tokens_details=_Attr(reasoning_tokens=25),
                 prompt_tokens_details=_Attr(cached_tokens=64))


# ---------- تست‌ها ----------
def test_usage_full():
    fc, logs = _install(_Resp("سلام", usage=_usage_full()))
    out = run(wt_brain._chat("sys", "usr", feature="task_evaluate"))
    r = logs[-1]
    ok = check("۱) usage کامل درست استخراج می‌شود",
               out == "سلام" and r["ok"] and r["in"] == 120 and r["out"] == 40
               and r["total"] == 160 and r["reasoning"] == 25 and r["cached"] == 64)
    ok &= check("۱ب) max از policyِ feature خوانده شد (2800)", fc.calls[0].get("max_completion_tokens") == 2800)
    return ok


def test_usage_missing():
    _fc, logs = _install(_Resp("متن", usage=None))
    out = run(wt_brain._chat("s", "u", feature="task_followup"))
    r = logs[-1]
    return check("۲) نبودِ usage → crash نمی‌کند و پاسخ سالم است",
                 out == "متن" and r["ok"] and r.get("in") is None and r.get("out") is None)


def test_usage_partial_shape():
    _fc, logs = _install(_Resp("x", usage=_Attr(prompt_tokens=10, completion_tokens=7, total_tokens=17)))
    run(wt_brain._chat("s", "u", feature="manager_reply"))
    r = logs[-1]
    return check("۳) usage با شکلِ متفاوت/ناقص → in/out خوانده، reasoning/cached=None",
                 r["in"] == 10 and r["out"] == 7 and r["reasoning"] is None and r["cached"] is None)


def test_error_logged_and_fallback():
    fc, logs = _install(raise_exc=RuntimeError("boom"))
    raised = False
    try:
        run(wt_brain._chat("s", "u", feature="manager_reply"))
    except RuntimeError:
        raised = True
    r = logs[-1]
    ok = check("۴) خطای مدل: raise شد + لاگِ ناموفق با نوعِ خطا",
               raised and r["ok"] is False and r["err"] == "RuntimeError")
    # fallbackِ فراخوان حفظ می‌شود: evaluate باید {} بدهد نه crash
    _fc2, _logs2 = _install(raise_exc=RuntimeError("boom"))
    ev = run(wt_brain.evaluate("علی", "", "", "گزارش", "qa"))
    ok &= check("۴ب) fallbackِ evaluate روی خطا حفظ شد ({} برمی‌گردد)", ev == {})
    ok &= check("۴ج) روی خطا فقط یک بار create صدا خورد (retry اضافه نشد)", len(_fc2.calls) == 1)
    return ok


def test_no_content_in_usage_log():
    _fc, logs = _install(_Resp("پاسخِ محرمانه", usage=_usage_full()))
    run(wt_brain._chat("SYSTEM-SECRET-PROMPT", "USER-PHONE-09120000000", feature="task_evaluate"))
    r = logs[-1]
    banned = ("system", "user", "messages", "content", "prompt", "response", "text")
    leaked = [k for k in r if k in banned]
    joined = " ".join(str(v) for v in r.values())
    return check("۵) هیچ prompt/پاسخ/محتوا در usage log نیست",
                 not leaked and "SECRET" not in joined and "09120000000" not in joined and "محرمانه" not in joined)


def test_policy_max_tokens_source():
    for feat, exp in (("task_followup", 1400), ("task_evaluate", 2800), ("manager_reply", 600),
                      ("issue_routing", 800), ("ig_content_plan", 12000)):
        p = config.wt_policy(feat)
        if p["max_output_tokens"] != exp:
            return check(f"۱۱) max policy برای {feat}", False)
    return check("۱۱) max output از policyِ feature خوانده می‌شود (اعدادِ فعلی حفظ شد)", True)


def test_policy_invalid_safe_default():
    saved = config.WT_MODEL_POLICY
    try:
        config.WT_MODEL_POLICY = {
            "bad_zero": {"max_output_tokens": 0, "effort": "banana", "timeout": -5},
            "bad_huge": {"max_output_tokens": 10 ** 9, "effort": "medium", "timeout": 999999},
        }
        z = config.wt_policy("bad_zero")
        h = config.wt_policy("bad_huge")
        unknown = config.wt_policy("does_not_exist")
        ok = check("۱۲) مقادیرِ نامعتبر → safe default",
                   z["max_output_tokens"] == config.WT_MAX_TOKENS and z["effort"] is None and z["timeout"] == 90.0
                   and h["max_output_tokens"] == config.WT_MAX_TOKENS and h["timeout"] == 90.0
                   and unknown["max_output_tokens"] == config.WT_MAX_TOKENS)
    finally:
        config.WT_MODEL_POLICY = saved
    return ok


def test_retry_not_repeated_on_invalid():
    fc, _logs = _install(raise_exc=ValueError("invalid request"))
    try:
        run(wt_brain._chat("s", "u", feature="issue_routing"))
    except ValueError:
        pass
    return check("۱۳) روی خطا retry تکرار نمی‌شود (دقیقاً ۱ فراخوانی)", len(fc.calls) == 1)


def test_structured_output_preserved():
    import json
    ev_json = json.dumps({"score": 82, "summary": "خوب", "tasks": [{"text": "t", "priority": "high", "kind": "sales"}],
                          "carryover": [], "remaining": [], "blockers": [], "growth_tips": [], "flags": []})
    _install(_Resp(ev_json, usage=_usage_full()))
    ev = run(wt_brain.evaluate("علی", "", "", "گزارش", "qa"))
    ok = check("۱۴) خروجیِ ساختاریافتهٔ evaluate بدونِ regression پارس می‌شود",
               isinstance(ev, dict) and ev.get("score") == 82 and ev.get("tasks"))
    mr_json = json.dumps({"ack": "چشم", "directive": "", "scope": "global", "target_hint": "",
                          "tasks": ["کارِ نو"], "edits": [], "close_task_ids": [3, 5], "correction": ""})
    _install(_Resp(mr_json, usage=_usage_full()))
    mr = run(wt_brain.interpret_manager_reply("bot", "reply"))
    ok &= check("۱۴ب) interpret_manager_reply ساختار را حفظ می‌کند (close_task_ids صحیح)",
                mr.get("tasks") == ["کارِ نو"] and mr.get("close_task_ids") == [3, 5])
    return ok


# ---------- تست‌های worktasks (routing قطعی + snapshotِ context) ----------
def test_deterministic_route():
    import worktasks as w
    staff = [(1, "مریم", "تولید محتوا اینستاگرام و استوری"),
             (2, "رضا", "انبار و بسته بندی و ارسال سفارش")]
    fresh_clear = [{"key": "k1", "text": "استوری اینستاگرام تولید نشد امروز"}]
    fresh_ambig = [{"key": "k2", "text": "سفارش جدید ثبت شد"}]         # واژهٔ مشترک با هیچ‌کدام کافی نیست
    det1 = w._deterministic_route(fresh_clear, staff)
    det2 = w._deterministic_route(fresh_ambig, staff)
    ok = check("۶) دستورِ واضح → matchِ قطعیِ یکتا (بدونِ LLM)", det1.get("k1") == "مریم")
    ok &= check("۷) موردِ مبهم/بی‌match → قطعی نیست (به LLM می‌رود)", "k2" not in det2)
    # خروجی هرگز نامی خارج از فهرستِ پرسنل نمی‌دهد (allowlist)
    names = {n for _u, n, _d in staff}
    ok &= check("۸) خروجیِ routing خارج از allowlistِ پرسنل نیست", set(det1.values()) <= names)
    # هم‌پوشانیِ مساوی بینِ دو نفر → قطعی نشود
    staff_tie = [(1, "الف", "عکس محصول"), (2, "ب", "عکس محصول")]
    det3 = w._deterministic_route([{"key": "k3", "text": "عکس محصول ناقص است"}], staff_tie)
    ok &= check("۸ب) دو نفر با امتیازِ مساوی → قطعی نمی‌شود", "k3" not in det3)
    return ok


def test_context_snapshot_single_fetch():
    import worktasks as w
    calls = {"n": 0}

    async def fake_staff(uid):
        calls["n"] += 1
        return f"staff-ctx-{uid}"

    saved = w._staff_context
    w._staff_context = fake_staff
    w._report_ctx.clear()
    try:
        rid, uid = 9991, 555
        first = run(w._staff_context_cycle(rid, uid))     # followup
        second = run(w._staff_context_cycle(rid, uid))    # evaluate (همان چرخه)
        ok = check("۹) در یک چرخهٔ followup/evaluate، provider فقط یک‌بار fetch می‌شود", calls["n"] == 1)
        ok &= check("۱۰) snapshot بینِ مصرف‌کننده‌ها بدونِ mutation و یکسان است",
                    first == second == "staff-ctx-555")
        # پاک‌سازیِ چرخه → build مجدد
        w._report_ctx.pop(rid, None)
        run(w._staff_context_cycle(rid, uid))
        ok &= check("۱۰ب) پس از پایانِ چرخه، snapshotِ جدید ساخته می‌شود", calls["n"] == 2)
    finally:
        w._staff_context = saved
        w._report_ctx.clear()
    return ok


def main():
    tests = [test_usage_full, test_usage_missing, test_usage_partial_shape, test_error_logged_and_fallback,
             test_no_content_in_usage_log, test_policy_max_tokens_source, test_policy_invalid_safe_default,
             test_retry_not_repeated_on_invalid, test_structured_output_preserved, test_deterministic_route,
             test_context_snapshot_single_fetch]
    results = []
    for t in tests:
        try:
            results.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            results.append(False)
    passed, total = sum(results), len(results)
    print(f"\n{passed}/{total} گروهِ تست سبز شد.")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
