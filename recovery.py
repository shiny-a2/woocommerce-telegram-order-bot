"""موتورِ بازیابیِ پرداختِ ناموفق.

سفارش‌های رهاشده (failed/pending) را می‌یابد، دیتای بازیابی (کوپن + لینکِ /go/) را از
‎/tg/recovery می‌خواند، پیامِ صمیمانه می‌سازد، و از طریقِ صفِ تراکنشیِ یوزربات (tg-outreach)
به مشتری می‌فرستد — دو مرحله (۳۰ دقیقه + فردا)، فقط در ساعتِ مجاز، توقف اگر پرداخت شد.

حالتِ test: همه‌ی پیام‌ها به RECOVERY_TEST_PHONE می‌رود (نه مشتریِ واقعی).
"""
from __future__ import annotations

import asyncio
import datetime
import time

import requests

import clock
import config
import crm
import db
import woo


def _toman(val) -> str:
    try:
        return f"{int(float(val or 0)) // config.MONEY_DIVISOR:,}"
    except Exception:
        return str(val or "0")


def _channel():
    """(url, status_url, header, token) بر اساسِ کانالِ بازیابی (واتساپ یا تلگرام)."""
    if config.RECOVERY_CHANNEL == "whatsapp":
        return config.WA_SEND_URL, config.WA_SEND_URL + "/status", "X-Token", config.WA_SEND_TOKEN
    return config.TXOUT_URL, config.TXOUT_URL + "/status", "X-Dash-Token", config.TXOUT_TOKEN


def _enqueue_sync(phone, text, key):
    url, _s, hdr, tok = _channel()
    r = requests.post(url, json={"phone": phone, "text": text, "key": key}, headers={hdr: tok}, timeout=12)
    r.raise_for_status()
    return r.json()


async def _enqueue(phone, text, key):
    return await asyncio.to_thread(_enqueue_sync, phone, text, key)


def _tx_status_sync(key):
    """وضعیتِ یک پیام در صف (sent / no_telegram / no_whatsapp / failed / optout / expired / pending / None)."""
    try:
        _u, surl, hdr, tok = _channel()
        r = requests.get(surl, params={"key": key}, headers={hdr: tok}, timeout=8)
        r.raise_for_status()
        return r.json().get("status")
    except Exception:
        return None  # نامعلوم → محتاطانه ادامه بده (مرحله‌۲ را رد نکن)


async def _tx_status(key):
    return await asyncio.to_thread(_tx_status_sync, key)


def _pay_link(order) -> str:
    """لینکِ ادامه‌ی پرداختِ همان سفارش (بدونِ تخفیف)."""
    key = order.get("order_key") or ""
    oid = order.get("id")
    if key and oid:
        return f"{config.WOO_URL}/checkout/order-pay/{oid}/?pay_for_order=true&key={key}"
    return ""


def _build_message(order, rec, stage) -> str:
    """پیامِ بازیابی (متنِ ساده). مرحله‌ی ۱ = تشویق بدونِ تخفیف؛ مرحله‌ی ۲ = کدِ تخفیف."""
    b = order.get("billing") or {}
    name = (b.get("first_name") or "").strip() or "دوست"
    items = order.get("line_items") or []
    product = (items[0].get("name") if items else "") or "سفارشتون"
    full = rec.get("amount_due") or order.get("total") or 0

    if stage == 1:  # تشویق، بدونِ تخفیف، لینکِ ادامه‌ی همان سفارش
        link = _pay_link(order) or rec.get("recover_url") or ""
        return "\n".join([
            f"سلام {name} عزیز 🌹",
            f"از {config.SHOP_NAME} مزاحمتون شدم. دیدیم خریدتون از «{product}» نیمه‌کاره موند و پرداخت کامل نشد 😊",
            "",
            "هیچ نگران نباشید — هر وقت خواستید با یک کلیک همون‌جا که بودید ادامه بدید:",
            f"🛍️ ادامه‌ی خرید: {link}",
            f"💳 مبلغِ سفارش: {_toman(full)} تومان",
            "",
            "اگه سوالی داشتید یا کمک خواستید، همین‌جا کنارتونیم 💛",
            f"با احترام، تیمِ {config.SHOP_NAME}",
        ])

    # مرحله‌ی ۲ — کدِ تخفیف + مبلغِ پس از تخفیف
    coupon = rec.get("coupon") or ""
    pct = int(rec.get("coupon_percent") or 0)
    exp = rec.get("expires_local") or ""
    url = rec.get("recover_url") or _pay_link(order) or ""
    lines = [
        f"سلام {name} عزیز 🌹",
        f"هنوز فرصت هست خریدتون از «{product}» رو کامل کنید 😊",
        "",
    ]
    if coupon and pct:
        try:
            after = int(float(full)) * (100 - pct) // 100
        except Exception:
            after = full
        exp_s = f"، تا {exp}" if exp else ""
        lines += [
            f"🎁 این‌بار یک هدیه هم براتون گذاشتیم: کدِ تخفیفِ «{coupon}» ({pct}٪{exp_s})",
            f"🔗 با این لینک تخفیف خودکار اعمال می‌شه: {url}",
            f"💳 مبلغِ پس از تخفیف: {_toman(after)} تومان",
        ]
    else:
        lines += [
            "هر وقت خواستید با یک کلیک ادامه بدید:",
            f"🔗 {url}",
            f"💳 مبلغِ سفارش: {_toman(full)} تومان",
        ]
    lines += [
        "",
        "خوشحال می‌شیم همراهیتون کنیم 💛",
        f"با احترام، تیمِ {config.SHOP_NAME}",
    ]
    return "\n".join(lines)


def _elapsed_min(date_created_gmt) -> float:
    """دقیقه‌های گذشته از ثبتِ سفارش (مبنا: UTC، مستقل از تایم‌زونِ سایت)."""
    try:
        dt = datetime.datetime.fromisoformat((date_created_gmt or "").replace("Z", ""))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return (clock.utcnow() - dt).total_seconds() / 60.0
    except Exception:
        return 0.0


async def tick(app):
    if config.RECOVERY_MODE not in ("test", "live") or not crm.enabled() or not _channel()[3]:
        return
    now = clock.tehran_now()
    now_e = time.time()
    within = config.RECOVERY_SEND_START <= now.hour < config.RECOVERY_SEND_END
    is_test = config.RECOVERY_MODE == "test"
    if is_test and not config.RECOVERY_TEST_PHONE:
        print("[recover] حالتِ تست ولی RECOVERY_TEST_PHONE خالی است — هیچ پیامی ارسال نمی‌شود.")
        return
    # اگر حالت (test/live) عوض شده، ردیف‌های پرداخت‌نشده را پاک کن تا مرحله‌ها قاطیِ هم نشوند
    if db.get_meta("recovery_mode_last") != config.RECOVERY_MODE:
        db.recovery_reset_unpaid()
        db.set_meta("recovery_mode_last", config.RECOVERY_MODE)

    # خط مبنا: اولین فعال‌سازی فقط زمان را ثبت می‌کند تا بک‌لاگِ قدیمی سیل‌آسا پیام نگیرد
    base = db.get_meta("recovery_baseline_ts")
    if base is None:
        db.set_meta("recovery_baseline_ts", str(now_e))
        print("[recover] خط مبنا تنظیم شد؛ فقط سفارش‌های رهاشده‌ی بعد از این لحظه بازیابی می‌شوند.")
        return
    base = float(base)

    # ۱) سفارش‌های رهاشده‌ی اخیر
    after = (now - datetime.timedelta(hours=config.RECOVERY_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%S")
    orders = []
    for st in config.RECOVERY_STATUSES:
        try:
            orders += await woo.get("orders", {
                "status": st, "per_page": 40, "after": after, "orderby": "date", "order": "desc",
                "_fields": "id,status,date_created_gmt,total,order_key,billing,line_items",
            })
        except Exception as e:
            print(f"[recover] گرفتنِ سفارش‌های {st}: {e!r}")

    sent_this_tick = 0
    for o in orders:
        oid = o.get("id")
        phone = (o.get("billing") or {}).get("phone")
        if not oid or not phone:
            continue
        elapsed = _elapsed_min(o.get("date_created_gmt"))
        created_e = now_e - elapsed * 60.0
        if created_e < base:  # سفارشِ قبل از فعال‌سازی → نادیده (ضدِسیلِ بک‌لاگ)
            continue
        db.recovery_ensure(oid, phone, created_e)
        row = db.recovery_row(oid)
        if not row or row["paid"]:
            continue
        stage = None
        if not row["sent1_at"] and elapsed >= config.RECOVERY_FIRST_DELAY_MIN:
            stage = 1
        elif row["sent1_at"] and not row["sent2_at"] and (now_e - row["sent1_at"]) >= config.RECOVERY_SECOND_DELAY_H * 3600:
            # اگر مرحله‌۱ اصلاً تحویل نشد (بی‌تلگرام/حریم‌خصوصی)، مرحله‌۲ بی‌فایده است → رد و پایان
            st1 = await _tx_status(f"rec:{oid}:1" + (":test" if is_test else ""))
            if st1 in ("no_telegram", "no_whatsapp", "failed", "optout", "expired", "invalid"):
                db.recovery_mark_sent(oid, 2)  # علامتِ پایان تا دیگر پردازش نشود
                print(f"[recover] سفارش {oid}: مرحله‌۱ {st1} → مرحله‌۲ رد شد (تحویل‌نشده).")
                continue
            stage = 2
        if not stage or not within:
            continue
        try:
            rec = await crm.recovery(order_id=oid)
        except Exception as e:
            print(f"[recover] /recovery {oid}: {e!r}")
            continue
        if rec.get("paid"):
            if row["sent1_at"]:  # فقط اگر واقعاً پیام رفته بود → بازیابیِ واقعی (نه پرداختِ ارگانیک)
                db.recovery_mark_paid(oid, rec.get("amount_due") or o.get("total"))
            continue
        link_ok = bool(_pay_link(o) or rec.get("recover_url"))  # مرحله‌ی ۲ کوپن را اگر بود می‌گذارد، وگرنه یادآوریِ ساده
        if not link_ok:
            continue
        text = _build_message(o, rec, stage)
        if is_test:
            text = f"🧪 [پیامِ تست — سفارش {oid}]\n" + text
        target = config.RECOVERY_TEST_PHONE if is_test else phone
        key = f"rec:{oid}:{stage}" + (":test" if is_test else "")
        try:
            res = await _enqueue(target, text, key)
            if res.get("added") or res.get("exists"):  # تازه صف شد یا از قبل بود → هر دو «صف‌شده» (ضدِگیرکردنِ مرحله)
                db.recovery_mark_sent(oid, stage)
                if res.get("added"):
                    sent_this_tick += 1
                print(f"[recover] مرحله‌ی {stage} سفارش {oid} در صفِ یوزربات ({'تست' if is_test else 'مشتری'}).")
        except Exception as e:
            print(f"[recover] enqueue {oid}: {e!r}")
        if sent_this_tick >= config.RECOVERY_MAX_PER_TICK:
            print(f"[recover] سقفِ {config.RECOVERY_MAX_PER_TICK} پیام در این چرخه پر شد؛ بقیه چرخه‌ی بعد.")
            break
        await asyncio.sleep(0.3)

    # ۲) بازبینیِ پرداختِ سفارش‌هایی که پیام گرفتند ولی هنوز paid نشده‌اند → ثبتِ درآمدِ بازیابی‌شده
    for oid, _phone in db.recovery_active(now_e - config.RECOVERY_WINDOW_H * 3600 * 2):  # ۲× پنجره برای پرداخت‌های دیرهنگام
        try:
            rec = await crm.recovery(order_id=oid)
            if rec.get("paid"):
                db.recovery_mark_paid(oid, rec.get("amount_due"))
                print(f"[recover] ✅ سفارش {oid} پرداخت شد — بازیابیِ موفق.")
        except Exception:
            pass
        await asyncio.sleep(0.2)
