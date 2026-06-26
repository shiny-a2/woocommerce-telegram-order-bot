"""خلاصه‌ی فروش «دیروز» را به گروه می‌فرستد. توسط یک تسک زمان‌بندی‌شده‌ی جدا هر صبح اجرا می‌شود."""
from __future__ import annotations

import asyncio
import datetime
import sys

import jdatetime
from telegram import Bot

import config
import reports
import woo

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main():
    bot = Bot(config.TELEGRAM_BOT_TOKEN)
    await woo.load_states()

    y = jdatetime.date.today() - datetime.timedelta(days=1)  # دیروز شمسی
    g = y.togregorian()
    start = datetime.datetime(g.year, g.month, g.day)
    end = start + datetime.timedelta(days=1)
    label = f"{reports.J_MONTHS[y.month - 1]} {y.day}، {y.year}"

    body = reports._format_report(label, *(await reports._aggregate(start, end)))
    await bot.send_message(chat_id=config.TELEGRAM_GROUP_ID, text="🌅 خلاصه‌ی فروش دیروز\n\n" + body)
    print("[daily] خلاصه‌ی دیروز ارسال شد.")


if __name__ == "__main__":
    asyncio.run(main())
