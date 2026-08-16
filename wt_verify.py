"""wt_verify.py — صحت‌سنجیِ قطعیِ rule-based برای تسک‌های سایت و اینستاگرام (Core Operational Release).

اصول:
- فقط ruleهای allowlist‌شده در کد (LLM حق ساختِ rule ندارد؛ هیچ ارزیابیِ «کیفیت/خوب‌بودن» خودکار نیست).
- read-only؛ هیچ نوشتنِ خودکار روی سایت/اینستاگرام. timeoutِ کوتاه، بدونِ pollingِ بی‌نهایت.
- یک fetch برای هر entity در هر cycle (cache). هیچ responseِ خامِ بزرگ/حساس ذخیره نمی‌شود — فقط نتیجه + refِ کوتاه.
- شکست/عدمِ‌دسترسیِ API → outcome=unavailable (تسک در claimed_done می‌ماند، رد/گم نمی‌شود).

قابلیت‌های واقعیِ کشف‌شده (source of truth = کد):
- سایت (READ): woo.get_product(id).status/stock_quantity، woo.get_order(id).status، woo.total_count(ep,params)،
  crm.activity(wp_id, from, to).counts. نوشتنِ خودکارِ سایت پیاده‌سازی نشده → فعال نمی‌شود.
- اینستاگرام (READ، صرفاً تجمیعی): igstats.summary() → media_count/posts_7d/... . شناسهٔ per-media/status/permalink
  در client موجود نیست → صحت‌سنجیِ یک پستِ مشخص پشتیبانی نمی‌شود (backlog). فقط ruleِ تجمیعیِ شمارش.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass


@dataclass
class VerificationResult:
    outcome: str        # positive | negative | unavailable
    source: str = ""    # website | instagram
    ref: str = ""       # مرجعِ کوتاهِ audit، مثلِ "product/123:publish" (بدونِ دادهٔ حساس)
    detail: str = ""    # دلیلِ کوتاهِ نمایش به مدیر (بدونِ responseِ خام)


# allowlistِ ruleها: نام → (source, پارامترهای لازم). LLM حق ساخت/تغییر ندارد.
RULE_SPECS = {
    "product_published":     ("website", ("entity_id",)),
    "product_stock_at_least": ("website", ("entity_id", "threshold")),
    "order_status_is":       ("website", ("entity_id", "expected")),
    "product_count_at_least": ("website", ("params", "threshold")),
    "crm_activity_at_least": ("website", ("wp_id", "action", "threshold")),
    "ig_posts_at_least":     ("instagram", ("threshold",)),
}
_IG_METRICS = {"media_count", "posts_7d", "posts_30d", "posts_24h"}


def validate_rule(rule) -> tuple[bool, str]:
    """اعتبارسنجیِ ساختاریِ rule در کد (هنگامِ ساختِ تسک). خروجی (ok, err)."""
    if not isinstance(rule, dict):
        return False, "rule not a dict"
    name = rule.get("rule")
    spec = RULE_SPECS.get(name)
    if not spec:
        return False, f"unknown rule {name!r}"
    for p in spec[1]:
        if rule.get(p) in (None, ""):
            return False, f"missing param {p}"
    if name == "ig_posts_at_least" and rule.get("metric", "media_count") not in _IG_METRICS:
        return False, "bad ig metric"
    return True, ""


def rule_source(rule) -> str:
    spec = RULE_SPECS.get((rule or {}).get("rule"))
    return spec[0] if spec else ""


def dumps_rule(rule) -> str:
    return json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads_rule(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


# ---------- آداپترهای واقعی (read-only wrapper دورِ clientهای موجود) ----------
class WebsiteAdapter:
    """آداپترِ واقعیِ سایت: صرفاً readهای موجودِ woo/crm را صدا می‌زند. هیچ writeای ندارد."""
    async def get_product(self, pid):
        import woo
        return await woo.get_product(pid)

    async def get_order(self, oid):
        import woo
        return await woo.get_order(oid)

    async def total_count(self, endpoint, params):
        import woo
        return await woo.total_count(endpoint, params)

    async def crm_activity(self, wp_id, date_from, date_to):
        import crm
        return await crm.activity(wp_id, date_from, date_to)


class InstagramAdapter:
    """آداپترِ واقعیِ اینستاگرام: فقط igstats.summary() (تجمیعیِ read-only). بدونِ login/write."""
    async def summary(self):
        import igstats
        return await igstats.summary()


# ---------- ارزیابیِ قطعی ----------
async def _await(coro, timeout):
    return await asyncio.wait_for(coro, timeout=timeout)


async def verify_rule(rule, website=None, instagram=None, cache=None, timeout=8.0) -> VerificationResult:
    """یک ruleِ allowlist‌شده را قطعی ارزیابی می‌کند. cache: dict مشترکِ cycle برای dedupِ fetch.

    مثبت/منفیِ قطعی → positive/negative؛ هر خطا/timeout/عدمِ‌دسترسی → unavailable (بدونِ رد کردنِ تسک).
    """
    ok, err = validate_rule(rule)
    if not ok:
        return VerificationResult("unavailable", detail=f"rule invalid: {err}")
    name = rule["rule"]
    source = RULE_SPECS[name][0]
    cache = cache if cache is not None else {}

    async def fetch(key, make_coro):
        if key in cache:                                   # یک fetch در هر cycle برای هر entity (بدونِ ساختنِ coroutineِ اضافه)
            return cache[key]
        val = await _await(make_coro(), timeout)
        cache[key] = val
        return val

    try:
        if name == "product_published":
            pid = rule["entity_id"]
            p = await fetch(("product", pid), lambda: website.get_product(pid))
            good = (p or {}).get("status") == "publish"
            return VerificationResult("positive" if good else "negative", "website",
                                      f"product/{pid}:status", "منتشر شد" if good else f"وضعیت={((p or {}).get('status'))}")
        if name == "product_stock_at_least":
            pid, thr = rule["entity_id"], int(rule["threshold"])
            p = await fetch(("product", pid), lambda: website.get_product(pid))
            sq = (p or {}).get("stock_quantity")
            good = sq is not None and int(sq) >= thr
            return VerificationResult("positive" if good else "negative", "website",
                                      f"product/{pid}:stock", f"موجودی={sq} (لازم≥{thr})")
        if name == "order_status_is":
            oid, exp = rule["entity_id"], str(rule["expected"])
            o = await fetch(("order", oid), lambda: website.get_order(oid))
            st = (o or {}).get("status")
            good = st == exp
            return VerificationResult("positive" if good else "negative", "website",
                                      f"order/{oid}:status", f"وضعیت={st} (لازم={exp})")
        if name == "product_count_at_least":
            params, thr = rule["params"], int(rule["threshold"])
            key = ("count", json.dumps(params, sort_keys=True))
            n = await fetch(key, lambda: website.total_count("products", params))
            good = n is not None and int(n) >= thr
            return VerificationResult("positive" if good else "negative", "website",
                                      "products:count", f"تعداد={n} (لازم≥{thr})")
        if name == "crm_activity_at_least":
            wp, action, thr = rule["wp_id"], str(rule["action"]), int(rule["threshold"])
            days = int(rule.get("days", 2))
            import clock
            import datetime
            now = clock.tehran_now()
            frm = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            to = now.strftime("%Y-%m-%d")
            a = await fetch(("crm", wp, frm, to), lambda: website.crm_activity(wp, frm, to))
            if not (a or {}).get("ok"):
                return VerificationResult("unavailable", "website", f"crm/{wp}", "پاسخِ CRM معتبر نبود")
            cnt = int(((a.get("counts") or {}).get(action)) or 0)
            good = cnt >= thr
            return VerificationResult("positive" if good else "negative", "website",
                                      f"crm/{wp}:{action}", f"{action}={cnt} (لازم≥{thr})")
        if name == "ig_posts_at_least":
            metric = rule.get("metric", "media_count")
            thr = int(rule["threshold"])
            s = await fetch(("ig_summary",), lambda: instagram.summary())
            if not (s or {}).get("ok"):
                return VerificationResult("unavailable", "instagram", "ig:summary", "دادهٔ اینستاگرام در دسترس نیست")
            val = s.get(metric)
            good = val is not None and int(val) >= thr
            return VerificationResult("positive" if good else "negative", "instagram",
                                      f"ig:{metric}", f"{metric}={val} (لازم≥{thr})")
    except asyncio.TimeoutError:
        return VerificationResult("unavailable", source, "", "timeout")
    except Exception as e:  # noqa: BLE001 — هر خطای API/شبکه → unavailable (تسک رد نمی‌شود)
        return VerificationResult("unavailable", source, "", f"api_error:{type(e).__name__}")
    return VerificationResult("unavailable", source, "", "unhandled rule")
