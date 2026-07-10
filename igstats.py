"""مصرف‌کننده‌ی گزارشِ آنالیزِ اینستاگرام از سرویسِ مستقلِ ig-insights.

ig-insights با کپیِ سشنِ اینستاگرام (بدونِ دست‌زدن به ربات اصلی) آمارِ واقعیِ رشد و تعامل را
جمع می‌کند. اینجا فقط /api/report آن را می‌گیریم و برای مدیران قالب می‌دهیم.
"""
from __future__ import annotations

import asyncio

import requests
from telegram.constants import ParseMode

import config

_TIMEOUT = (4, 12)
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(_FA)


def enabled() -> bool:
    return bool(config.IG_INSIGHTS_URL)


def _get_sync() -> dict:
    params = {"token": config.IG_INSIGHTS_TOKEN} if config.IG_INSIGHTS_TOKEN else {}
    r = requests.get(f"{config.IG_INSIGHTS_URL}/api/report", params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


async def summary() -> dict:
    """گزارشِ آنالیزِ اینستاگرام از ig-insights (فقط‌خواندنی). fail-soft."""
    if not enabled():
        return {"ok": False, "error": "disabled"}
    try:
        return await asyncio.to_thread(_get_sync)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def _dd(v) -> str:
    if v is None:
        return "—"
    if v > 0:
        return f"▲ {_fa(v)}"
    if v < 0:
        return f"▼ {_fa(abs(v))}"
    return "۰"


_MT = {1: "📷 عکس", 2: "🎬 ریل", 8: "🖼 آلبوم"}


def format_report(r: dict) -> str:
    if not r.get("ok"):
        return "📊 آنالیزِ اینستاگرام هنوز آماده نیست (سرویسِ ig-insights پاسخ نداد یا اولین جمع‌آوری انجام نشده)."
    lines = [
        f"📊 <b>آنالیزِ پیجِ اینستاگرام</b> @{r.get('username', '')}",
        "",
        f"👥 فالوور: <b>{_fa(r.get('followers', 0))}</b> · رشدِ امروز: <b>{_dd(r.get('growth_1d'))}</b> · ۷روز: <b>{_dd(r.get('growth_7d'))}</b>",
        f"📝 پست: <b>{_fa(r.get('media_count', 0))}</b> · ۲۴ساعت: {_fa(r.get('posts_24h', 0))} · ۷روز: {_fa(r.get('posts_7d', 0))}",
        f"❤️ میانگینِ تعاملِ پست‌های اخیر: <b>{_fa(r.get('avg_engagement', 0))}</b> · نرخِ تعامل: <b>{r.get('engagement_rate', 0)}%</b>",
    ]
    if r.get("insighted_posts"):
        lines.append(
            f"👁 ریچِ {_fa(r.get('insighted_posts', 0))} پستِ اخیر: <b>{_fa(r.get('total_reach', 0))}</b> "
            f"(میانگین {_fa(r.get('avg_reach', 0))}) · 🔖 سیو: {_fa(r.get('total_saves', 0))}")
        lines.append(
            f"➕ فالوِ جذب‌شده از پست‌ها: <b>{_fa(r.get('total_follows_from_posts', 0))}</b> · "
            f"👤 بازدیدِ پروفایل: {_fa(r.get('total_profile_views', 0))}")
    b = r.get("best_reach_post") or r.get("best_post") or {}
    if b:
        tag = _MT.get(b.get("media_type"), "")
        extra = f" · 👁{_fa(b.get('reach', 0))}" if b.get("reach") else ""
        lines.append(f"⭐ بهترین پست: {tag} ❤️{_fa(b.get('like_count', 0))} 💬{_fa(b.get('comment_count', 0))}{extra}")
    if r.get("last_collect"):
        lines.append("")
        lines.append(f"<i>آخرین به‌روزرسانی: {r['last_collect']}</i>")
    lines.append("<i>نمای کامل: داشبوردِ ig-insights روی پورت ۸۰۹۶</i>")
    return "\n".join(lines)


async def cmd_igreport(update, context):
    """آنالیزِ پیجِ اینستاگرام — رشد/تعامل (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if user.id not in config.ADMIN_USER_IDS:
        await msg.reply_text("این گزارش فقط برای مدیران است.")
        return
    r = await summary()
    await msg.reply_text(format_report(r), parse_mode=ParseMode.HTML)
