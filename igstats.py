"""آنالیزِ اینستاگرام از API محلیِ فقط‌خواندنیِ سرویسِ لاگین‌شده (ig-assistant روی :8092).

قانونِ ایمنی: این بات هرگز به اینستاگرام لاگین نمی‌کند، سشن نمی‌خواند/کپی نمی‌کند و کلاینتِ
instagrapi/مرورگر نمی‌سازد (سشنِ دوم = چالش/بن). فقط از IG_DASH_URL (کش‌دار) می‌خواند، اسنپ‌شاتِ
فالوور/تعامل را در دیتابیسِ خودش ذخیره می‌کند و رشد را روی همان دادهٔ ذخیره‌شده حساب می‌کند.
بدونِ force؛ poll حداکثر ~ساعتی یک‌بار.
"""
from __future__ import annotations

import asyncio
import datetime
import time

import requests
from telegram.constants import ParseMode

import clock
import config
import db

_TIMEOUT = (4, 15)
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_SNAP_EVERY = 3000  # حداقل فاصلهٔ ثبتِ اسنپ‌شات (~۵۰ دقیقه) — رعایتِ سقفِ poll


def _fa(n) -> str:
    return str(n).translate(_FA)


def enabled() -> bool:
    return bool(config.IG_DASH_URL)


def _get_sync(path, params=None):
    p = dict(params or {})
    if config.IG_DASH_TOKEN:
        p["token"] = config.IG_DASH_TOKEN
    r = requests.get(f"{config.IG_DASH_URL}{path}", params=p, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _all_sync(media_limit=30):
    d = _get_sync("/api/analytics/all", {"media_limit": media_limit})  # کش‌شده، بدونِ force
    if not isinstance(d, dict) or not d.get("ok"):
        raise RuntimeError("dash not ok")
    return d.get("bundle") or {}


_TYPE = {"photo": "📷 عکس", "image": "📷 عکس", "video": "🎬 ویدیو/ریل",
         "clips": "🎬 ریل", "carousel": "🖼 آلبوم", "album": "🖼 آلبوم", "igtv": "🎬 ویدیو"}


def _age_days(taken_at) -> float:
    try:
        dt = datetime.datetime.fromisoformat(str(taken_at).replace("Z", "+00:00"))
        base = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
        return (base - dt).total_seconds() / 86400
    except Exception:  # noqa: BLE001
        return 1e9


def _derive(bundle: dict) -> dict:
    """باندلِ خامِ :8092 → گزارشِ آنالیز + ثبتِ اسنپ‌شات + محاسبهٔ رشد از تاریخچه."""
    ov = (bundle.get("overview") or {}).get("data") or {}
    md = (bundle.get("media") or {}).get("data") or {}
    st = (bundle.get("stories") or {}).get("data") or {}
    au = ((bundle.get("audience") or {}).get("data") or {}).get("summary") or {}
    items = md.get("items") or []
    followers = ov.get("followers") or md.get("followers") or 0
    media_count = ov.get("media_count") or 0

    # اسنپ‌شات (با گاردِ فاصله) → تاریخچه برای رشد
    if followers and time.time() - (db.ig_last_snapshot_ts() or 0) >= _SNAP_EVERY:
        db.ig_snapshot_add(followers, media_count, md.get("avg_engagement"), md.get("avg_engagement_rate"))
    f1, f7 = db.ig_followers_ago(86400), db.ig_followers_ago(7 * 86400)
    growth_1d = (followers - f1) if (followers and f1 is not None) else None
    growth_7d = (followers - f7) if (followers and f7 is not None) else None

    ages = [_age_days(it.get("taken_at")) for it in items]
    posts_24h = sum(1 for a in ages if a <= 1)
    posts_7d = sum(1 for a in ages if a <= 7)

    best = None
    if items:
        b = max(items, key=lambda it: (it.get("engagement") or 0))
        best = {"type": b.get("type"), "like_count": b.get("likes") or 0,
                "comment_count": b.get("comments") or 0, "engagement": b.get("engagement") or 0,
                "url": b.get("url")}

    return {
        "ok": True,
        "username": ov.get("username"),
        "followers": followers,
        "media_count": media_count,
        "growth_1d": growth_1d,
        "growth_7d": growth_7d,
        "posts_24h": posts_24h,
        "posts_7d": posts_7d,
        "avg_engagement": round(md.get("avg_engagement") or 0),
        "engagement_rate": md.get("avg_engagement_rate") or 0,
        "best_post": best,
        "best_reach_post": None,  # ریچِ تک‌پست فقط با درخواستِ جدا (/insights) — در آنالیزِ دوره‌ای نمی‌گیریم
        "stories_live": st.get("count") or 0,
        "profile_visits": au.get("profile_visits"),
        "profile_visits_delta": au.get("profile_visits_delta"),
        "website_visits": au.get("website_visits"),
        "reach": au.get("reach"),
        "last_collect": clock.tehran_now().strftime("%m-%d %H:%M"),
    }


async def summary() -> dict:
    """گزارشِ آنالیزِ اینستاگرام (فقط‌خواندنی از :8092). fail-soft — هیچ‌گاه لاگین نمی‌کند."""
    if not enabled():
        return {"ok": False, "error": "disabled"}
    try:
        bundle = await asyncio.to_thread(_all_sync, 30)
        return _derive(bundle)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


async def maybe_snapshot():
    """poll کش‌شدهٔ ~ساعتی برای ساختِ تاریخچهٔ رشد (بدونِ force). از پولر صدا زده می‌شود."""
    if not enabled() or time.time() - (db.ig_last_snapshot_ts() or 0) < _SNAP_EVERY:
        return
    try:
        await summary()  # خودش اسنپ‌شات را ثبت می‌کند
    except Exception:  # noqa: BLE001
        pass


def _dd(v) -> str:
    if v is None:
        return "—"
    if v > 0:
        return f"▲ {_fa(v)}"
    if v < 0:
        return f"▼ {_fa(abs(v))}"
    return "۰"


def format_report(r: dict) -> str:
    if not r.get("ok"):
        return "📊 آنالیزِ اینستاگرام فعلاً در دسترس نیست (سرویسِ آنالیز پاسخ نداد). کمی بعد دوباره امتحان کن."
    L = [
        f"📊 <b>آنالیزِ پیجِ اینستاگرام</b> @{r.get('username', '')}",
        "",
        f"👥 فالوور: <b>{_fa(r.get('followers', 0))}</b> · رشدِ ۱روز: <b>{_dd(r.get('growth_1d'))}</b> · ۷روز: <b>{_dd(r.get('growth_7d'))}</b>",
        f"📝 پست: <b>{_fa(r.get('media_count', 0))}</b> · ۲۴ساعت: {_fa(r.get('posts_24h', 0))} · ۷روز: {_fa(r.get('posts_7d', 0))}",
        f"❤️ میانگینِ تعاملِ پست‌های اخیر: <b>{_fa(r.get('avg_engagement', 0))}</b> · نرخِ تعامل: <b>{r.get('engagement_rate', 0)}%</b>",
    ]
    if r.get("stories_live"):
        L.append(f"📸 استوریِ فعالِ الان: <b>{_fa(r.get('stories_live', 0))}</b>")
    if r.get("profile_visits") is not None:
        d = r.get("profile_visits_delta")
        dd = f" ({_dd(d)})" if d is not None else ""
        L.append(f"👤 بازدیدِ پروفایل: <b>{_fa(r.get('profile_visits', 0))}</b>{dd}"
                 f" · 🔗 کلیکِ سایت: {_fa(r.get('website_visits') or 0)}")
    if r.get("reach"):
        L.append(f"👁 ریچِ اکانت: <b>{_fa(r.get('reach'))}</b>")
    b = r.get("best_post") or {}
    if b:
        tag = _TYPE.get(b.get("type"), "پست")
        L.append(f"⭐ بهترین پستِ اخیر: {tag} ❤️{_fa(b.get('like_count', 0))} 💬{_fa(b.get('comment_count', 0))}")
    growth = r.get("growth_1d")
    if growth is None:
        L += ["", "<i>رشدِ روزانه پس از انباشتِ چند اسنپ‌شات (چند ساعت) نمایش داده می‌شود.</i>"]
    if r.get("last_collect"):
        L.append(f"<i>به‌روزرسانی: {r['last_collect']}</i>")
    return "\n".join(L)


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
