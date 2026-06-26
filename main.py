"""نقطه‌ی ورود: ربات تلگرام + پولینگ (+ وب‌هوک اختیاری) در یک لوپ.

با خودترمیمی: اگر اجرای اصلی به هر دلیل بیفتد، خودش پس از چند ثانیه دوباره بالا می‌آید.
خروجی روی data/bot.log نوشته می‌شود (چون به‌صورت سرویس بدون کنسول اجرا می‌شود).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from telegram.ext import Application

import config
import db
import poller
import telegram_io
import woo

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "data", "bot.log")


def _setup_logging():
    try:
        os.makedirs(os.path.join(_HERE, "data"), exist_ok=True)
        mode = "a"
        if os.path.exists(_LOG) and os.path.getsize(_LOG) > 2_000_000:
            mode = "w"  # چرخش ساده وقتی لاگ بزرگ شد
        stream = open(_LOG, mode, encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        pass


async def _ensure_baseline():
    """خط مبنا: فقط سفارش‌هایی با آیدیِ بزرگ‌تر از این مقدار پست می‌شوند؛ سفارش‌های
    قدیمیِ موجود هرگز پست نمی‌شوند (جلوگیری از backfill/اسپم)."""
    if db.get_meta("baseline_id") is not None:
        return
    try:
        orders = await woo.list_recent_orders(per_page=1)
        baseline = orders[0].get("id") if orders else 0
    except Exception as e:
        print(f"[baseline] تعیین خط مبنا ناموفق بود: {e}")
        baseline = 0
    db.set_meta("baseline_id", baseline)
    print(f"[baseline] خط مبنا روی {baseline} تنظیم شد؛ فقط سفارش‌های جدیدتر پست می‌شوند.")


async def main():
    missing = [
        k
        for k, v in {
            "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_GROUP_ID": config.TELEGRAM_GROUP_ID,
            "WOO_URL": config.WOO_URL,
            "WOO_CK": config.WOO_CK,
            "WOO_CS": config.WOO_CS,
        }.items()
        if not v
    ]
    if missing:
        raise SystemExit("این متغیرها در .env تنظیم نشده‌اند: " + ", ".join(missing))

    db.init()
    await woo.load_states()
    await _ensure_baseline()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    telegram_io.register_handlers(app)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("[bot] ربات تلگرام فعال شد.")

    tasks = [asyncio.create_task(poller.run(app))]
    if config.WEBHOOK_ENABLED:
        import webhook_server

        tasks.append(asyncio.create_task(webhook_server.serve(app)))

    try:
        await asyncio.gather(*tasks)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    _setup_logging()
    os.chdir(_HERE)
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[fatal] خطای کلی: {e} — ۱۵ ثانیه دیگر تلاش مجدد", flush=True)
            time.sleep(15)
