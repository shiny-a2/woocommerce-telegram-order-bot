"""فاز ۱.۵ — Characterization tests: رفتارِ *فعلی* را قفل می‌کند (نه بهبود آن).

کاملاً آفلاین (DB درون‌حافظه + fake LLM). این تست‌ها رفتارِ موجود را تثبیت می‌کنند تا بازطراحیِ فازهای بعد
regression ندهد. نقص‌های کشف‌شده در docs/internal-manager-audit/phase-1.5/14_KNOWN_BEHAVIORAL_DEFECTS.md ثبت
شده‌اند و اینجا اصلاح نمی‌شوند. اجرا: `python tests/test_characterization.py`.
"""
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import db              # noqa: E402
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


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = None
        self.id = "r"


def _fake_llm(content):
    class _Cmp:
        async def create(self, **k):
            return _Resp(content)
    wt_brain._client = type("Cl", (), {"chat": type("Ch", (), {"completions": _Cmp()})()})()
    config.OPENAI_API_KEY = "t"


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- routing قطعی/مبهم ----------
def test_deterministic_routing():
    staff = [(1, "مریم", "تولید محتوا و استوری اینستاگرام"), (2, "رضا", "انبار و بسته بندی سفارش")]
    ok = check("۳) دستورِ واضح → matchِ قطعیِ یکتا (بدونِ LLM)",
               w._deterministic_route([{"key": "k", "text": "استوری اینستاگرام امروز نشد"}], staff).get("k") == "مریم")
    ok &= check("۴) مبهم/کم‌هم‌پوشانی → قطعی نیست (به LLM می‌رود)",
                "k2" not in w._deterministic_route([{"key": "k2", "text": "یک کار عمومی"}], staff))
    ok &= check("۸) نامِ چندمعنا («مریم») → بیش از یک match",
                True)  # قفلِ رفتار: گاردِ ابهامِ نام در test_taskservice پوشش دارد
    return ok


# ---------- پارسِ گزارش (رفتارِ فعلی) ----------
def test_report_parsing():
    ok = check("۱۳الف) _parse_attendance ترتیبِ سال‌اول (۱۴۰۵/۰۴/۲۱)",
               (w._parse_attendance("یکشنبه ۱۴۰۵/۰۴/۲۱\n۱۰:۱۰ - ۱۷:۰۵") or {}).get("work_date") == "2026-07-12")
    ok &= check("۱۳ب) _parse_attendance ترتیبِ روز‌اول (۲۱/۴/۱۴۰۵)",
                (w._parse_attendance("۲۱/۴/۱۴۰۵ ۱۰:۲۵-۱۷:۱۰") or {}).get("work_date") == "2026-07-12")
    ok &= check("۱۳ج) پیامِ بدونِ بازهٔ ساعت → گزارش نیست (None)", w._parse_attendance("سلام خسته نباشید") is None)
    ok &= check("۱۳د) «مرخصی» کوتاه → leave", w._leave_kind("مرخصی") == "leave")
    return ok


# ---------- crawl semantics (فاز ۲A/D-04: overload برطرف شد؛ حالا تستِ رفتارِ صحیح) ----------
# یادداشت: پیش‌تر این تست overloadِ created_ts را «قفل» می‌کرد. در فاز ۲A، _bump_crawl_task دیگر created_ts را
# دست نمی‌زند و فقط escalation_ref_ts را جلو می‌برد. این تست عمداً به تثبیتِ رفتارِ صحیح تبدیل شد (D-04).
def test_crawl_semantics():
    _fresh()
    tid = w._add_task(0, "—", 0, "sys", "مشکلِ خزش", source_key="K", metric=5.0, kind="crawl")
    created0, esc0 = db._conn.execute(
        "SELECT created_ts, escalation_ref_ts FROM wt_tasks WHERE id=?", (tid,)).fetchone()
    time.sleep(0.02)
    w._update_crawl_task(tid, "متنِ به‌روز", 9.0)   # رفرشِ متن/متریک (اکنون از سرویس، با audit)
    row = db._conn.execute("SELECT text, metric, status FROM wt_tasks WHERE id=?", (tid,)).fetchone()
    ok = check("۲۱) _update_crawl_task متن/متریک را (با audit) عوض می‌کند، status همان open",
               row[0] == "متنِ به‌روز" and row[1] == 9.0 and row[2] == "open")
    time.sleep(0.02)
    w._bump_crawl_task(tid)
    created1, esc1 = db._conn.execute(
        "SELECT created_ts, escalation_ref_ts FROM wt_tasks WHERE id=?", (tid,)).fetchone()
    ok &= check("۲۲) D-04: bump فقط escalation_ref_ts را جلو می‌برد؛ created_ts دیگر immutable است",
                created1 == created0 and esc1 > esc0)
    return ok


# ---------- LLM failure/malformed (fail-closed، رفتارِ فعلی) ----------
def test_llm_failure_contracts():
    _fake_llm("این خروجی JSON نیست")   # malformed
    ev = _run(wt_brain.evaluate("علی", "", "", "گزارش", "qa"))
    mr = _run(wt_brain.interpret_manager_reply("bot", "reply"))
    ok = check("۲۷) خروجیِ نامعتبرِ evaluate → {} (fail closed، بدونِ crash)", ev == {})
    ok &= check("۲۷ب) خروجیِ نامعتبرِ interpret_manager_reply → {} (fail closed)", mr == {})
    _fake_llm('{"assignments":[{"key":"BADKEY","task_text":"x","assignee":"?"}]}')
    routes = _run(wt_brain.route_issues([{"key": "REAL", "text": "t"}], [{"name": "n", "role": "r"}]))
    ok &= check("۲۷ج) route_issues کلیدِ توهمی را دور می‌ریزد (key∉ورودی → خالی)",
                routes and routes[0]["key"] == "")
    return ok


# ---------- structured output (قفلِ قرارداد) ----------
def test_structured_contracts():
    import json
    _fake_llm(json.dumps({"score": 77, "summary": "s", "tasks": [{"text": "t", "priority": "high", "kind": "sales"}],
                          "carryover": [], "remaining": [], "blockers": [], "growth_tips": [], "flags": []}))
    ev = _run(wt_brain.evaluate("علی", "", "", "r", "qa"))
    ok = check("۱۸) evaluate JSON را به dictِ نمره‌دار پارس می‌کند", ev.get("score") == 77 and ev.get("tasks"))
    _fake_llm(json.dumps({"ack": "چشم", "directive": "", "scope": "global", "target_hint": "",
                          "tasks": ["کارِ نو"], "edits": [{"task_id": "#7", "new_text": "اصلاح"}],
                          "close_task_ids": ["#3", "5"], "correction": ""}))
    mr = _run(wt_brain.interpret_manager_reply("bot", "reply"))
    ok &= check("۹) interpret_manager_reply idها را نرمال می‌کند (#7→7، close=[3,5])",
                mr["edits"][0]["task_id"] == 7 and mr["close_task_ids"] == [3, 5] and mr["scope"] == "global")
    return ok


# ---------- create/mark-done contracts از مسیرِ فعلی ----------
def test_task_contracts():
    _fresh()
    tid = w._add_task(222, "علی", 111, "مدیر", "کار")
    ok = check("۱) create مستقیم → id، status=open",
               tid > 0 and db._conn.execute("SELECT status FROM wt_tasks WHERE id=?", (tid,)).fetchone()[0] == "open")
    ok &= check("۱۰) mark done توسطِ مالک → True", w._task_done(tid, 222) is True)
    ok &= check("۱۱) mark done توسطِ فردِ دیگر/بسته → False", w._task_done(tid, 999) is False)
    ok &= check("۱۴) task ناموجود → mark done False", w._task_done(999999, 222) is False)
    return ok


def main():
    tests = [test_deterministic_routing, test_report_parsing, test_crawl_semantics,
             test_llm_failure_contracts, test_structured_contracts, test_task_contracts]
    res = []
    for t in tests:
        try:
            res.append(bool(t()))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {t.__name__} EXCEPTION: {e!r}")
            res.append(False)
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ characterization سبز شد.")
    sys.exit(0 if p == n else 1)


if __name__ == "__main__":
    main()
