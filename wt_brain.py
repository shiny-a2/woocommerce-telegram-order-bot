"""مغزِ اختصاصیِ ارزیابیِ عملکرد (OpenAI gpt-5.5) — فقط برای ماژولِ گزارشِ کار.

از همان کلیدِ OpenAI (config.OPENAI_API_KEY) با مدلِ config.WT_MODEL استفاده می‌کند.
اگر کلید نباشد یا خطا بدهد، fail-soft است (رشته/دیکشنریِ خالی) و ماژول بی‌AI کار می‌کند.
"""
from __future__ import annotations

import json

import config

_client = None


def enabled() -> bool:
    return bool(config.OPENAI_API_KEY)


def _client_():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


async def _chat(system: str, user: str, max_tokens: int) -> str:
    m = config.WT_MODEL
    kwargs = {
        "model": m,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if m.startswith(("gpt-5", "o1", "o3", "o4")):  # مدل‌های استدلالی: temperature نمی‌گیرند
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = 0.4
        kwargs["max_tokens"] = max_tokens
    r = await _client_().chat.completions.create(**kwargs)
    return (r.choices[0].message.content or "").strip()


async def followup_questions(name: str, done: str, opent: str, report: str) -> str:
    """حداکثر ۲ سؤالِ کوتاهِ پیگیرانه بر اساسِ تسک‌ها و گزارش (فارسی، شماره‌دار)."""
    if not enabled():
        return ""
    system = (
        "تو «دستیارِ مدیریتیِ» یک فروشگاهِ ایرانی هستی و منصف و محترمی. بر اساسِ تسک‌ها و گزارشِ کارمند، "
        "حداکثر ۲ سؤالِ کوتاه و مشخص بپرس که کیفیت و نتیجه‌ی واقعیِ کارِ امروزش را روشن کند "
        "(مثلاً نتیجه‌ی یک تسک، دلیلِ عقب‌افتادن، یا سندِ کار). فقط سؤال‌ها را فارسی و شماره‌دار بنویس، بدونِ مقدمه و نتیجه‌گیری."
    )
    user = f"کارمند: {name}\nتسک‌های انجام‌شده‌ی امروز: {done}\nتسک‌های باز: {opent}\nگزارشِ خودش: {report}"
    try:
        return await _chat(system, user, 400)
    except Exception as e:
        print(f"[wt_brain] followup_questions خطا: {e!r}")
        return ""


async def evaluate(name: str, done: str, opent: str, report: str, qa: str) -> dict:
    """نمره (۰–۱۰۰) + خلاصه‌ی یک‌جمله‌ای + پرچم‌ها. خروجی: {score, summary, flags}."""
    if not enabled():
        return {}
    system = (
        "تو ارزیابِ منصفِ عملکردِ روزانه‌ی کارمندانِ یک فروشگاه هستی. با توجه به تسک‌های محول/انجام‌شده، "
        "گزارشِ کارمند و پاسخ‌هایش، عملکردِ امروزش را بسنج. نه سخت‌گیرِ بی‌جا، نه سهل‌گیر. "
        "فقط و فقط یک JSON برگردان با کلیدهای دقیقِ: "
        'score (عددِ صحیحِ ۰ تا ۱۰۰), summary (یک جمله‌ی فارسیِ کوتاه و عینی), '
        "flags (آرایه‌ای از رشته‌های کوتاهِ هشدار برای مدیر، یا آرایه‌ی خالی). بدونِ متنِ اضافه."
    )
    user = (
        f"کارمند: {name}\nتسک‌های انجام‌شده‌ی امروز: {done}\nتسک‌های باز: {opent}\n"
        f"گزارشِ خودش: {report}\nسؤال‌وجوابِ ارزیابی: {qa}"
    )
    try:
        raw = (await _chat(system, user, 500)).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw[:4].lower() == "json":
                raw = raw[4:]
        d = json.loads(raw)
        return {
            "score": max(0, min(100, int(d.get("score", 0)))),
            "summary": str(d.get("summary", "")).strip(),
            "flags": [str(x).strip() for x in (d.get("flags") or []) if str(x).strip()],
        }
    except Exception as e:
        print(f"[wt_brain] evaluate خطا: {e!r}")
        return {}
