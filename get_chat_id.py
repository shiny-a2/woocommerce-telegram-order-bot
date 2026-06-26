"""کمک‌ابزار: ربات را در گروه عضو کن، یک پیام بفرست، بعد این را اجرا کن
تا آیدی گروه (برای TELEGRAM_GROUP_ID) و آیدی کاربری خودت (برای ADMIN_USER_IDS) را ببینی."""
from __future__ import annotations

import asyncio
import sys

from telegram import Bot

import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main():
    bot = Bot(config.TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    print(f"ربات: @{me.username} (id={me.id})")
    updates = await bot.get_updates(timeout=5)
    if not updates:
        print("هیچ پیامی دیده نشد. ربات را در گروه عضو کن، یک پیام بفرست، بعد دوباره اجرا کن.")
        return
    seen = set()
    for u in updates:
        msg = u.message or u.edited_message or u.channel_post
        if not msg:
            continue
        c = msg.chat
        sender = msg.from_user
        key = (c.id, sender.id if sender else None)
        if key in seen:
            continue
        seen.add(key)
        title = c.title or (c.full_name if hasattr(c, "full_name") else "")
        who = f"کاربر id={sender.id} ({sender.full_name})" if sender else "—"
        print(f"چت: نوع={c.type} | id={c.id} | عنوان={title} | فرستنده: {who}")


if __name__ == "__main__":
    asyncio.run(main())
