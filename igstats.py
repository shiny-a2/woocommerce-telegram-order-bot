"""آنالیزِ متخصص‌محورِ اینستاگرام از API محلیِ فقط‌خواندنیِ سرویسِ لاگین‌شده (ig-assistant روی :8092).

قانونِ ایمنی: این بات هرگز به اینستاگرام لاگین نمی‌کند، سشن نمی‌خواند/کپی نمی‌کند و کلاینتِ
instagrapi/مرورگر نمی‌سازد (سشنِ دوم = چالش/بن). فقط از IG_DASH_URL (کش‌دار، بدونِ force) می‌خواند،
اسنپ‌شاتِ فالوور/تعامل را ذخیره می‌کند و آنالیز (نوعِ محتوا، بهترین ساعت/روز، روندِ تعامل، کادنس،
کپشن، رشد) را روی همان دادهٔ ذخیره‌شده/بازگشتی انجام می‌دهد. poll حداکثر ~ساعتی.
"""
from __future__ import annotations

import asyncio
import datetime
import re
from collections import defaultdict

import requests
from telegram.constants import ParseMode

import clock
import config
import db

_TIMEOUT = (4, 15)
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_SNAP_EVERY = 3000  # حداقل فاصلهٔ ثبتِ اسنپ‌شات (~۵۰ دقیقه) — رعایتِ سقفِ poll
_TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))  # تهران
# python weekday(): دوشنبه=۰ … یک‌شنبه=۶
_WD = {0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه", 5: "شنبه", 6: "یک‌شنبه"}
_TYPE = {"photo": "📷 عکس", "image": "📷 عکس", "video": "🎬 ویدیو/ریل", "clips": "🎬 ریل",
         "carousel": "🖼 آلبوم", "album": "🖼 آلبوم", "igtv": "🎬 ویدیو"}

# واژه‌نامهٔ برندهای ساعت (نامِ نمایشی → نام‌های محتمل در کپشن، فارسی/انگلیسی). برای سنجشِ پوششِ برند.
_BRANDS = {
    "Casio": ["casio", "کاسیو", "g-shock", "gshock", "جی شاک", "جی‌شاک", "baby-g", "بیبی جی", "بیبی‌جی", "edifice"],
    "Omega": ["omega", "امگا", "سواچ", "swatch", "moonswatch"],
    "Rolex": ["rolex", "رولکس"], "Seiko": ["seiko", "سیکو"], "Citizen": ["citizen", "سیتیزن"],
    "Daniel Klein": ["daniel klein", "دنیل کلین", "دنیل‌کلین", "dk"],
    "Emporio Armani": ["armani", "آرمانی", "emporio"], "DKNY": ["dkny", "دی کی ان وای", "دی‌کی‌ان‌وای"],
    "Skagen": ["skagen", "اسکاگن"], "Fossil": ["fossil", "فسیل"], "Michael Kors": ["michael kors", "مایکل کورس", "mk"],
    "Guess": ["guess", "گس"], "Tissot": ["tissot", "تیسوت"], "Longines": ["longines", "لونژین"],
    "Pierre Lannier": ["pierre lannier", "پیر لنون", "پیرلنون", "pl"], "Elegance": ["elegance", "الگنس", "الگنگس"],
    "Trussardi": ["trussardi", "تروساردی"], "Curren": ["curren", "کوریو", "کورن"], "Winner": ["winner", "واینر"],
    "Naviforce": ["naviforce", "ناوی فورس", "ناوی‌فورس"], "Diesel": ["diesel", "دیزل"],
    "Hugo Boss": ["hugo boss", "هوگو باس", "boss"], "Calvin Klein": ["calvin klein", "کالوین کلاین", "ck"],
    "Versace": ["versace", "ورساچه"], "Gucci": ["gucci", "گوچی"], "Ferrari": ["ferrari", "فراری"],
}


_BOUND = "0-9a-z؀-ۿ‌"  # حروف/ارقامِ لاتین+فارسی+ZWNJ (برای مرزِ واژه)


def _detect_brands(caption: str) -> set:
    """برندهای ساعت را با «مرزِ واژه» تشخیص می‌دهد تا زیررشته گیر نکند (مثلاً «گس» داخلِ «الگنگس»)."""
    c = (caption or "").lower()
    found = set()
    for brand, aliases in _BRANDS.items():
        for a in aliases:
            if re.search(rf"(?<![{_BOUND}]){re.escape(a)}(?![{_BOUND}])", c):
                found.add(brand)
                break
    return found


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


def _all_sync(media_limit=50):
    d = _get_sync("/api/analytics/all", {"media_limit": media_limit})  # کش‌شده، بدونِ force
    if not isinstance(d, dict) or not d.get("ok"):
        raise RuntimeError("dash not ok")
    return d.get("bundle") or {}


def _tehran(taken_at):
    dt = datetime.datetime.fromisoformat(str(taken_at).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(_TZ)


def _avg(v):
    return round(sum(v) / len(v)) if v else 0


def _analyze_media(items) -> dict:
    """آنالیزِ محتوا مثلِ متخصصِ اینستاگرام: نوعِ محتوا، بهترین ساعت/روز، روندِ تعامل، کادنس، کپشن."""
    now = datetime.datetime.now(_TZ)
    posts = []
    for it in items:
        try:
            dt = _tehran(it.get("taken_at"))
        except Exception:  # noqa: BLE001
            dt = None
        eng = it.get("engagement")
        if eng is None:
            eng = (it.get("likes") or 0) + (it.get("comments") or 0)
        posts.append({"dt": dt, "type": (it.get("type") or "").lower(), "eng": int(eng or 0),
                      "likes": it.get("likes") or 0, "comments": it.get("comments") or 0,
                      "caption": it.get("caption") or "", "url": it.get("url")})
    res = {"sample": len(posts)}

    # عملکرد به تفکیکِ نوعِ محتوا
    byt = defaultdict(list)
    for p in posts:
        if p["type"]:
            byt[p["type"]].append(p["eng"])
    res["by_type"] = {t: {"count": len(v), "avg_eng": _avg(v)} for t, v in byt.items()}
    if res["by_type"]:
        bt = max(res["by_type"].items(), key=lambda kv: kv[1]["avg_eng"])
        res["best_type"] = {"type": bt[0], **bt[1]}

    # بهترین ساعت و روزِ انتشار (تهران؛ فقط سطل‌های با ≥۲ پست)
    byh, bywd = defaultdict(list), defaultdict(list)
    for p in posts:
        if p["dt"]:
            byh[p["dt"].hour].append(p["eng"])
            bywd[p["dt"].weekday()].append(p["eng"])
    ha = {h: sum(v) / len(v) for h, v in byh.items() if len(v) >= 2}
    if ha:
        h = max(ha, key=ha.get)
        res["best_hour"] = {"hour": h, "avg_eng": round(ha[h])}
    wa = {w: sum(v) / len(v) for w, v in bywd.items() if len(v) >= 2}
    if wa:
        w = max(wa, key=wa.get)
        res["best_weekday"] = {"name": _WD.get(w, "—"), "avg_eng": round(wa[w])}

    # کادنس
    res["posts_7d"] = sum(1 for p in posts if p["dt"] and (now - p["dt"]).days < 7)
    res["posts_30d"] = sum(1 for p in posts if p["dt"] and (now - p["dt"]).days < 30)

    # روندِ تعامل (نیمهٔ جدید نسبت به نیمهٔ قدیم)
    dated = sorted((p for p in posts if p["dt"]), key=lambda p: p["dt"])
    if len(dated) >= 6:
        half = len(dated) // 2
        oa = _avg([p["eng"] for p in dated[:half]])
        na = _avg([p["eng"] for p in dated[half:]])
        if oa > 0:
            res["eng_trend_pct"] = round((na - oa) / oa * 100)

    # کپشن (طول + هشتگ)
    caps = [p["caption"] for p in posts if p["caption"]]
    if caps:
        res["avg_caption_len"] = _avg([len(c) for c in caps])
        res["avg_hashtags"] = round(sum(c.count("#") for c in caps) / len(caps), 1)

    # پوششِ برندِ ساعت + کپشن‌های اخیر (خوراکِ مدیرِ محتواییِ متخصصِ ساعت)
    brand_count = defaultdict(int)
    recent_caps = []
    for p in sorted((x for x in posts if x["dt"]), key=lambda x: x["dt"], reverse=True):
        for b in _detect_brands(p["caption"]):
            brand_count[b] += 1
        if len(recent_caps) < 12 and p["caption"]:
            recent_caps.append(p["caption"][:140].replace("\n", " ").strip())
    res["brand_coverage"] = dict(sorted(brand_count.items(), key=lambda kv: -kv[1]))
    res["recent_captions"] = recent_caps

    # بهترین پستِ اخیر
    if posts:
        tp = max(posts, key=lambda p: p["eng"])
        res["top_post"] = {"type": tp["type"], "likes": tp["likes"], "comments": tp["comments"],
                           "eng": tp["eng"], "url": tp["url"]}
    return res


def _recommendations(r: dict) -> list:
    """توصیه‌های عملیِ داده‌محور برای رشد؛ هرکدام key پایدار (برای تسک‌سازی/ددآپ) + priority + metric."""
    recs = []
    bh = r.get("best_hour")
    bt = r.get("best_type")
    time_hint = f" بهترین ساعت ~{_fa(bh['hour'])}:۰۰" if bh else ""
    type_hint = f" و {_TYPE.get(bt['type'], bt['type'])} بهترین بازده را دارد" if bt else ""

    if (r.get("posts_24h") or 0) == 0:
        recs.append({"key": "ig_nopost", "priority": "high", "metric": 0,
                     "text": f"امروز هیچ پستی گذاشته نشده — یک پست/ریلِ محصول بگذار.{time_hint}{type_hint}"})
    p7 = r.get("posts_7d")
    if p7 is not None and p7 < 3:
        recs.append({"key": "ig_cadence", "priority": "high", "metric": p7,
                     "text": f"کادنسِ انتشار پایین است ({_fa(p7)} پست در ۷ روز) — هدف: حداقل ۳–۵ پست/هفته "
                             f"+ استوریِ روزانه.{time_hint}{type_hint}"})
    tr = r.get("eng_trend_pct")
    if tr is not None and tr <= -15:
        recs.append({"key": "ig_eng_drop", "priority": "high", "metric": abs(tr),
                     "text": f"تعاملِ پست‌های اخیر {_fa(abs(tr))}٪ افت کرده — قلابِ ۳ ثانیهٔ اول، کاور و CTA را قوی‌تر کن؛"
                             f" روی فرمتِ پربازده تمرکز کن.{type_hint}"})
    elif tr is not None and tr >= 20:
        recs.append({"key": "ig_eng_up", "priority": "low", "metric": tr,
                     "text": f"تعامل {_fa(tr)}٪ رشد کرده 👏 — همین فرمول/سبکِ اخیر را ادامه بده و بیشترش کن."})
    if bt and len(r.get("by_type") or {}) >= 2:
        others = [v["avg_eng"] for k, v in r["by_type"].items() if k != bt["type"] and v["avg_eng"]]
        if others and bt["avg_eng"] >= 1.4 * (sum(others) / len(others)):
            recs.append({"key": "ig_content_mix", "priority": "med", "metric": bt["avg_eng"],
                         "text": f"{_TYPE.get(bt['type'], bt['type'])} به‌وضوح بیشترین تعامل را می‌گیرد "
                                 f"(میانگین {_fa(bt['avg_eng'])}) — سهمِ آن را در برنامهٔ هفته بیشتر کن."})
    if not r.get("stories_live"):
        recs.append({"key": "ig_nostory", "priority": "med", "metric": 0,
                     "text": "الان استوریِ فعالی نداری — روزانه ۳–۵ استوری (نظرسنجی/سؤال/بک‌استیج) ریچ و تعامل را بالا می‌برد."})
    ah = r.get("avg_hashtags")
    if ah is not None and ah < 3:
        recs.append({"key": "ig_hashtags", "priority": "low", "metric": ah,
                     "text": f"میانگینِ هشتگ کم است ({_fa(ah)}) — ۵–۱۰ هشتگِ مرتبطِ برند/محصول/دسته اضافه کن."})
    g7 = r.get("growth_7d")
    if g7 is not None and g7 <= 0:
        recs.append({"key": "ig_growth", "priority": "high", "metric": abs(g7),
                     "text": "رشدِ فالوورِ ۷روزه صفر/منفی است — یک حرکتِ جذب اجرا کن (ریلزِ ترند، همکاری/کولب، "
                             "مسابقه یا کدِ تخفیفِ استوری‌محور)."})
    bc = r.get("brand_coverage") or {}
    if (r.get("posts_7d") or 0) >= 3 and 0 < len(bc) <= 2:
        recs.append({"key": "ig_brand_mix", "priority": "med", "metric": len(bc),
                     "text": "پست‌های اخیر فقط حولِ «" + "، ".join(list(bc)[:2]) + "» بوده — برندهای متنوع‌ترِ "
                             "موجودِ فروشگاه را هم بچرخان تا طیفِ مخاطبِ بیشتری جذب شود."})
    return recs


def _derive(bundle: dict) -> dict:
    ov = (bundle.get("overview") or {}).get("data") or {}
    md = (bundle.get("media") or {}).get("data") or {}
    st = (bundle.get("stories") or {}).get("data") or {}
    au = ((bundle.get("audience") or {}).get("data") or {}).get("summary") or {}
    items = md.get("items") or []
    followers = ov.get("followers") or md.get("followers") or 0
    media_count = ov.get("media_count") or 0

    if followers and _t_now() - (db.ig_last_snapshot_ts() or 0) >= _SNAP_EVERY:
        db.ig_snapshot_add(followers, media_count, md.get("avg_engagement"), md.get("avg_engagement_rate"))
    f1, f7, f30 = db.ig_followers_ago(86400), db.ig_followers_ago(7 * 86400), db.ig_followers_ago(30 * 86400)
    growth_1d = (followers - f1) if (followers and f1 is not None) else None
    growth_7d = (followers - f7) if (followers and f7 is not None) else None
    growth_30d = (followers - f30) if (followers and f30 is not None) else None

    ana = _analyze_media(items)
    posts_24h = sum(1 for it in items if _age_days(it.get("taken_at")) <= 1)

    r = {
        "ok": True,
        "username": ov.get("username"),
        "followers": followers,
        "following": ov.get("following"),
        "media_count": media_count,
        "category": ov.get("category"),
        "growth_1d": growth_1d, "growth_7d": growth_7d, "growth_30d": growth_30d,
        "posts_24h": posts_24h,
        "posts_7d": ana.get("posts_7d", 0),
        "posts_30d": ana.get("posts_30d", 0),
        "avg_engagement": round(md.get("avg_engagement") or 0),
        "engagement_rate": md.get("avg_engagement_rate") or 0,
        "by_type": ana.get("by_type") or {},
        "best_type": ana.get("best_type"),
        "best_hour": ana.get("best_hour"),
        "best_weekday": ana.get("best_weekday"),
        "eng_trend_pct": ana.get("eng_trend_pct"),
        "avg_caption_len": ana.get("avg_caption_len"),
        "avg_hashtags": ana.get("avg_hashtags"),
        "top_post": ana.get("top_post"),
        "best_post": ana.get("top_post"),          # سازگاری با خزنده
        "best_reach_post": None,
        "brand_coverage": ana.get("brand_coverage") or {},
        "recent_captions": ana.get("recent_captions") or [],
        "stories_live": st.get("count") or 0,
        "profile_visits": au.get("profile_visits"),
        "profile_visits_delta": au.get("profile_visits_delta"),
        "website_visits": au.get("website_visits"),
        "reach": au.get("reach"),
        "sample": ana.get("sample", 0),
        "last_collect": clock.tehran_now().strftime("%m-%d %H:%M"),
    }
    r["recommendations"] = _recommendations(r)
    return r


def _t_now():
    import time
    return time.time()


def _age_days(taken_at) -> float:
    try:
        dt = _tehran(taken_at)
        return (datetime.datetime.now(_TZ) - dt).total_seconds() / 86400
    except Exception:  # noqa: BLE001
        return 1e9


async def summary() -> dict:
    """آنالیزِ کاملِ اینستاگرام (فقط‌خواندنی از :8092). fail-soft — هیچ‌گاه لاگین نمی‌کند."""
    if not enabled():
        return {"ok": False, "error": "disabled"}
    try:
        bundle = await asyncio.to_thread(_all_sync, 50)
        return _derive(bundle)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


async def maybe_snapshot():
    """poll کش‌شدهٔ ~ساعتی برای ساختِ تاریخچهٔ رشد (بدونِ force). از پولر صدا زده می‌شود."""
    if not enabled() or _t_now() - (db.ig_last_snapshot_ts() or 0) < _SNAP_EVERY:
        return
    try:
        await summary()
    except Exception:  # noqa: BLE001
        pass


async def instock_by_brand(sample=100) -> dict:
    """محصولاتِ موجودِ سایت (stock=instock) گروه‌بندی بر اساسِ برندِ تشخیص‌داده‌شده از نامِ محصول.

    خروجی: {brand: {"count": n, "examples": [نام/رفرنس]}} — برای پوششِ رفرنس‌های واقعیِ قابلِ عکاسی در تقویم.
    """
    import woo
    try:
        items = await woo.get("products", {
            "stock_status": "instock", "status": "publish", "per_page": min(int(sample), 100),
            "orderby": "date", "order": "desc", "_fields": "id,name,sku"})
    except Exception as e:  # noqa: BLE001
        print(f"[igstats] instock fetch: {e!r}")
        return {}
    by = defaultdict(lambda: {"count": 0, "examples": []})
    for p in (items or []):
        name = (p.get("name") or "").strip()
        for b in _detect_brands(name):
            by[b]["count"] += 1
            if len(by[b]["examples"]) < 4:
                sku = p.get("sku")
                by[b]["examples"].append(name[:42] + (f" [{sku}]" if sku else ""))
    return dict(sorted(by.items(), key=lambda kv: -kv[1]["count"]))


# ---------- برای ارزیابیِ ادمینِ اینستاگرام (خطِ فشردهٔ واقعیت‌ها) ----------
def facts_line(r: dict) -> str:
    if not r or not r.get("ok"):
        return ""
    parts = [f"پستِ ۲۴ساعت={r.get('posts_24h', 0)}", f"پستِ ۷روز={r.get('posts_7d', 0)}"]
    g1, g7 = r.get("growth_1d"), r.get("growth_7d")
    parts.append("رشدِ فالوورِ ۱روز=" + ("؟" if g1 is None else str(g1)))
    parts.append("۷روز=" + ("؟" if g7 is None else str(g7)))
    tr = r.get("eng_trend_pct")
    if tr is not None:
        parts.append(f"روندِ تعامل={tr:+d}٪")
    bt = r.get("best_type")
    if bt:
        parts.append(f"بهترین‌نوع={bt['type']}")
    bh = r.get("best_hour")
    if bh:
        parts.append(f"بهترین‌ساعت={bh['hour']}")
    parts.append(f"استوریِ فعال={r.get('stories_live', 0)}")
    if r.get("profile_visits") is not None:
        parts.append(f"بازدیدِ پروفایل={r.get('profile_visits')}")
    return "کارِ واقعیِ اینستاگرام (آنالیزِ پیج): " + "، ".join(parts)


def _dd(v) -> str:
    if v is None:
        return "—"
    return f"▲ {_fa(v)}" if v > 0 else (f"▼ {_fa(abs(v))}" if v < 0 else "۰")


def format_report(r: dict) -> str:
    if not r.get("ok"):
        return "📊 آنالیزِ اینستاگرام فعلاً در دسترس نیست (سرویسِ آنالیز پاسخ نداد). کمی بعد دوباره امتحان کن."
    L = [f"📊 <b>آنالیزِ پیجِ اینستاگرام</b> @{r.get('username', '')}", ""]
    L.append(f"👥 فالوور: <b>{_fa(r.get('followers', 0))}</b> · "
             f"رشد ۱روز <b>{_dd(r.get('growth_1d'))}</b> · ۷روز <b>{_dd(r.get('growth_7d'))}</b> · "
             f"۳۰روز <b>{_dd(r.get('growth_30d'))}</b>")
    L.append(f"📝 انتشار: کل {_fa(r.get('media_count', 0))} · ۲۴ساعت {_fa(r.get('posts_24h', 0))} · "
             f"۷روز {_fa(r.get('posts_7d', 0))} · ۳۰روز {_fa(r.get('posts_30d', 0))}")
    tr = r.get("eng_trend_pct")
    trs = f" · روند {('▲' if tr > 0 else '▼') if tr else ''}{_fa(abs(tr))}٪" if tr is not None else ""
    L.append(f"❤️ میانگینِ تعامل: <b>{_fa(r.get('avg_engagement', 0))}</b> · نرخ {r.get('engagement_rate', 0)}%{trs}")

    # نوعِ محتوا
    if r.get("by_type"):
        seg = " · ".join(f"{_TYPE.get(t, t)} {_fa(v['avg_eng'])} ({_fa(v['count'])})"
                         for t, v in sorted(r["by_type"].items(), key=lambda kv: -kv[1]["avg_eng"]))
        L.append(f"🎯 تعامل به‌تفکیکِ نوع: {seg}")
    bc = r.get("brand_coverage") or {}
    if bc:
        L.append("🏷️ پوششِ برند (اخیر): " + " · ".join(f"{b}({_fa(c)})" for b, c in list(bc.items())[:7]))
    tips = []
    if r.get("best_hour"):
        tips.append(f"⏰ بهترین ساعت ~{_fa(r['best_hour']['hour'])}:۰۰")
    if r.get("best_weekday"):
        tips.append(f"📅 بهترین روز {r['best_weekday']['name']}")
    if r.get("avg_hashtags") is not None:
        tips.append(f"#️⃣ میانگینِ هشتگ {_fa(r.get('avg_hashtags'))}")
    if tips:
        L.append("🧭 " + " · ".join(tips))

    if r.get("stories_live"):
        L.append(f"📸 استوریِ فعال: <b>{_fa(r.get('stories_live'))}</b>")
    if r.get("profile_visits") is not None:
        d = r.get("profile_visits_delta")
        L.append(f"👤 بازدیدِ پروفایل: <b>{_fa(r.get('profile_visits'))}</b>"
                 f"{f' ({_dd(d)})' if d is not None else ''} · 🔗 کلیکِ سایت {_fa(r.get('website_visits') or 0)}"
                 + (f" · 👁 ریچ {_fa(r.get('reach'))}" if r.get('reach') else ""))
    tp = r.get("top_post") or {}
    if tp:
        L.append(f"⭐ بهترین پستِ اخیر: {_TYPE.get(tp.get('type'), 'پست')} "
                 f"❤️{_fa(tp.get('likes', 0))} 💬{_fa(tp.get('comments', 0))}")

    recs = r.get("recommendations") or []
    if recs:
        L += ["", "💡 <b>توصیه‌های رشد (متخصص‌محور):</b>"]
        for rec in recs[:6]:
            L.append(f"• {rec['text']}")

    if r.get("growth_1d") is None:
        L += ["", "<i>روند/رشدِ فالوور پس از انباشتِ چند اسنپ‌شات (چند ساعت–روز) کامل می‌شود.</i>"]
    if r.get("last_collect"):
        L.append(f"<i>به‌روزرسانی: {r['last_collect']} · نمونهٔ آنالیز: {_fa(r.get('sample', 0))} پست</i>")
    return "\n".join(L)


async def cmd_igreport(update, context):
    """آنالیزِ متخصص‌محورِ پیجِ اینستاگرام — رشد/تعامل/محتوا/توصیه (فقط مدیر)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if user.id not in config.ADMIN_USER_IDS:
        await msg.reply_text("این گزارش فقط برای مدیران است.")
        return
    r = await summary()
    await msg.reply_text(format_report(r), parse_mode=ParseMode.HTML)


# ---------- رقبا (آنالیزِ دادهٔ عمومیِ رقبا از API فقط‌خواندنی؛ بنچمارک + ایده) ----------
_RIVAL_MIN_AGE = 20 * 3600  # هر رقیب حداکثر ~روزی یک‌بار (جمع‌آوریِ آهسته/انسانی سمتِ سرویسِ صاحبِ سشن)


async def competitor(handle: str) -> dict:
    """دادهٔ عمومیِ یک رقیب از اندپوینتِ /api/analytics/competitor (اگر سرویسِ صاحبِ سشن فعالش کرده باشد).

    این بات هرگز خودش تماسِ مستقیمِ اینستاگرام نمی‌زند؛ فقط از API محلی می‌خواند. fail-soft: تا فعال‌شدنِ
    اندپوینت، ok=False برمی‌گردد.
    """
    if not enabled():
        return {"ok": False, "error": "disabled"}
    try:
        d = await asyncio.to_thread(_get_sync, "/api/analytics/competitor", {"username": handle})
        if not isinstance(d, dict) or not d.get("ok"):
            return {"ok": False, "error": "unavailable"}
        return {"ok": True, "data": d.get("data") or {}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def _analyze_rival(d: dict) -> dict:
    items = d.get("recent_media") or d.get("items") or []
    engs = []
    for it in items:
        e = it.get("engagement")
        engs.append(int((e if e is not None else (it.get("likes") or 0) + (it.get("comments") or 0)) or 0))
    ana = _analyze_media(items)
    return {"followers": d.get("followers") or 0, "following": d.get("following"),
            "media_count": d.get("media_count"), "posts_7d": ana.get("posts_7d", 0),
            "avg_eng": _avg(engs), "by_type": ana.get("by_type") or {},
            "best_hour": ana.get("best_hour"), "brand_coverage": ana.get("brand_coverage") or {},
            "avg_hashtags": ana.get("avg_hashtags"), "top_post": ana.get("top_post"),
            "recent_captions": ana.get("recent_captions") or []}


async def collect_rival(handle: str):
    """یک رقیب را می‌گیرد، آنالیز و اسنپ‌شاتِ رشدش را ذخیره می‌کند. None اگر در دسترس نبود."""
    c = await competitor(handle)
    if not c.get("ok"):
        return None
    m = _analyze_rival(c["data"])
    if m["followers"]:
        db.rival_snap_add(handle, m["followers"], m["posts_7d"], m["avg_eng"])
    g = db.rival_followers_ago(handle, 7 * 86400)
    m["growth_7d"] = (m["followers"] - g) if (m["followers"] and g is not None) else None
    m["handle"] = handle
    m["ok"] = True
    return m


async def maybe_collect_rival():
    """جمع‌آوریِ چرخشیِ آهسته: هر بار فقط «کهنه‌ترین» رقیب (حداکثر ~روزی یک‌بار در هر رقیب)."""
    if not enabled():
        return
    h = db.rival_due_for_collect(_RIVAL_MIN_AGE)
    if h:
        await collect_rival(h)


async def rivals_report() -> dict:
    """بنچمارکِ همهٔ رقبا نسبت به پیجِ خودمان (از دادهٔ کش‌شده). خروجی برای فرمت و برای تقویمِ محتوایی."""
    hs = db.rivals()
    mine = await summary()
    out = {"mine": mine if mine.get("ok") else None, "rivals": [], "collected": 0}
    for h in hs:
        m = await collect_rival(h)
        if m:
            out["rivals"].append(m)
            out["collected"] += 1
        else:
            out["rivals"].append({"handle": h, "ok": False})
    return out


def format_rivals(rep: dict) -> str:
    rivals_all = rep.get("rivals") or []
    if not rivals_all:
        return "🏁 هنوز رقیبی اضافه نشده. با <code>/rivals add آیدی</code> اضافه کن."
    L = ["🏁 <b>بنچمارکِ رقبا</b>", ""]
    mine = rep.get("mine")
    if mine:
        L += [f"⭐ <b>ما</b> @{mine.get('username', '')}: فالوور {_fa(mine.get('followers', 0))} · "
              f"پستِ۷روز {_fa(mine.get('posts_7d', 0))} · تعامل {_fa(mine.get('avg_engagement', 0))}", ""]
    ok = sorted([r for r in rivals_all if r.get("ok")], key=lambda r: -(r.get("followers") or 0))
    for r in ok:
        g = r.get("growth_7d")
        gt = f" · رشدِ۷روز {_dd(g)}" if g is not None else ""
        bt = ""
        if r.get("by_type"):
            best = max(r["by_type"].items(), key=lambda kv: kv[1]["avg_eng"])
            bt = f" · قوی‌ترین‌نوع {_TYPE.get(best[0], best[0])}"
        L.append(f"• <b>@{r['handle']}</b>: فالوور {_fa(r.get('followers', 0))} · "
                 f"پستِ۷روز {_fa(r.get('posts_7d', 0))} · تعامل {_fa(r.get('avg_eng', 0))}{gt}{bt}")
    pend = [r["handle"] for r in rivals_all if not r.get("ok")]
    if pend:
        L += ["", f"⏳ هنوز جمع‌آوری‌نشده ({_fa(len(pend))}): " + "، ".join("@" + h for h in pend[:12]),
              "<i>پس از فعال‌شدنِ اندپوینتِ رقبا در سرویسِ اینستاگرام، خودکار پر می‌شود.</i>"]
    return "\n".join(L)


def rivals_brief(rep: dict) -> str:
    """خلاصهٔ فشردهٔ رقبا برای خوراکِ مدیرِ محتوایی (جلوزدن از رقبا)."""
    ok = [r for r in (rep.get("rivals") or []) if r.get("ok")]
    if not ok:
        return ""
    parts = []
    for r in sorted(ok, key=lambda r: -(r.get("avg_eng") or 0))[:6]:
        bt = ""
        if r.get("by_type"):
            best = max(r["by_type"].items(), key=lambda kv: kv[1]["avg_eng"])
            bt = f"، قوی‌ترین‌نوع={best[0]}"
        bc = "،".join(list((r.get("brand_coverage") or {}).keys())[:4])
        parts.append(f"@{r['handle']}: فالوور {r.get('followers')}، پستِ۷روز {r.get('posts_7d')}، "
                     f"تعامل {r.get('avg_eng')}{bt}، برندها[{bc}]")
    return "؛ ".join(parts)
