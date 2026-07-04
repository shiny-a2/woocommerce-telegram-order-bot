"""نقطه‌ی ورود: ربات تلگرام (run_polling) + پولینگ پس‌زمینه + وب‌هوک اختیاری.

خودترمیم: اگر اجرا به هر دلیلی بیفتد، پس از چند ثانیه دوباره بالا می‌آید.
خروجی با تایم‌استمپِ تهران روی data/bot.log نوشته می‌شود.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from telegram import BotCommand, Update
from telegram.ext import Application

import clock
import config
import db
import poller
import telegram_io
import woo
import worktasks

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "data", "bot.log")


class _Stamped:
    """هر خط خروجی را با زمانِ تهران برچسب می‌زند."""

    def __init__(self, stream):
        self._s = stream
        self._line_start = True

    def write(self, text):
        if not text:
            return
        ts = clock.tehran_now().strftime("%m-%d %H:%M:%S")
        out = []
        for piece in text.splitlines(keepends=True):
            if self._line_start:
                out.append(f"[{ts}] ")
            out.append(piece)
            self._line_start = piece.endswith("\n")
        self._s.write("".join(out))

    def flush(self):
        self._s.flush()


def _setup_logging():
    try:
        os.makedirs(os.path.join(_HERE, "data"), exist_ok=True)
        mode = "a"
        if os.path.exists(_LOG) and os.path.getsize(_LOG) > 2_000_000:
            mode = "w"
        stream = _Stamped(open(_LOG, mode, encoding="utf-8", buffering=1))
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        pass


async def _ensure_baseline():
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


async def _on_error(update, context):
    print(f"[ptb-error] {context.error!r}")


_COMMANDS = [
    BotCommand("menu", "منوی گزارش‌ها و فروش"),
    BotCommand("crm", "کارت مشتری با شماره — مثل: /crm 0912…"),
    BotCommand("range", "گزارش فروش در بازه‌ی دلخواه شمسی"),
    BotCommand("setfollowup", "ثبت این گروه به‌عنوان گروه پیگیری"),
]


async def _post_init(app):
    """پس از راه‌اندازیِ ربات: دیتابیس، استان‌ها، خط مبنا، دستورها، و تسکِ پولینگ پس‌زمینه."""
    db.init()
    worktasks.wt_init()  # جدول‌های گزارشِ کار (روی همان دیتابیس)
    await woo.load_states()
    await _ensure_baseline()
    try:
        await app.bot.set_my_commands(_COMMANDS)
    except Exception as e:
        print(f"[bot] ثبت دستورها ناموفق بود: {e}")
    app.bot_data["_poller"] = asyncio.create_task(poller.run(app))
    if config.WEBHOOK_ENABLED:
        import webhook_server

        app.bot_data["_webhook"] = asyncio.create_task(webhook_server.serve(app))
    print("[bot] ربات تلگرام فعال شد.")


def _build_app():
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    app.add_error_handler(_on_error)
    telegram_io.register_handlers(app)
    return app


def _env_missing():
    return [
        k for k, v in {
            "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_GROUP_ID": config.TELEGRAM_GROUP_ID,
            "WOO_URL": config.WOO_URL,
            "WOO_CK": config.WOO_CK,
            "WOO_CS": config.WOO_CS,
        }.items() if not v
    ]


if __name__ == "__main__":
    _setup_logging()
    os.chdir(_HERE)
    clock.refresh_sync()
    print("[boot] راه‌اندازی سرویس…")
    while True:
        missing = _env_missing()
        if missing:
            print("[main] متغیرهای .env ناقص‌اند: " + ", ".join(missing))
            time.sleep(15)
            continue
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            _build_app().run_polling(
                drop_pending_updates=True,
                stop_signals=None,
                close_loop=False,
                allowed_updates=Update.ALL_TYPES,  # صریح: کلیک دکمه‌ها (callback_query) هم تحویل بگیر
            )
        except KeyboardInterrupt:
            break
        except BaseException as e:
            print(f"[fatal] {e!r} — ۱۵ ثانیه دیگر تلاش مجدد")
            try:
                time.sleep(15)
            except Exception:
                pass
        finally:
            try:
                loop.close()
            except Exception:
                pass
