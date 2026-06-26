"""خط پردازش سفارش: درج سفارش جدید + ویرایش کپشنِ سفارش‌های تغییریافته.

کپشن یک پیامِ «زنده» است: با هر تغییر وضعیت یا اصلاح پلاگین، به‌جای ریپلای،
کپشن همان پیام بازسازی و در صورت اختلاف ویرایش می‌شود.
"""
from __future__ import annotations

import asyncio

import config
import db
import media
import plugin_events
import telegram_io
import woo

_locks: dict[int, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _order_lock(order_id) -> asyncio.Lock:
    async with _locks_guard:
        if order_id not in _locks:
            _locks[order_id] = asyncio.Lock()
        return _locks[order_id]


def _should_post(order) -> bool:
    if config.POST_STATUSES:
        return order.get("status") in config.POST_STATUSES
    return order.get("status") != "trash"


def _stock_location(product, qty):
    """فروشگاه‌ها اگر موجودیِ مدیریت‌شده و ≥۱ (قبل از سفارش) باشد، وگرنه شرکتی/انبار."""
    if not product:
        return None
    if not product.get("manage_stock") or product.get("stock_quantity") is None:
        return "شرکتی"
    try:
        before = float(product.get("stock_quantity")) + float(qty or 0)
    except (TypeError, ValueError):
        return "شرکتی"
    return "فروشگاه‌ها" if before >= 1 else "شرکتی"


async def _safe_notes(order_id):
    try:
        return await woo.get_notes(order_id)
    except Exception as e:
        print(f"[pipeline] یادداشت سفارش {order_id} گرفته نشد: {e}")
        return []


async def process_order(app, order_id: int):
    lock = await _order_lock(order_id)
    async with lock:
        if db.is_posted(order_id):
            return
        order = await woo.get_order(order_id)
        if not _should_post(order):
            return

        photos = []
        locations = []
        for it in (order.get("line_items") or [])[: config.MAX_PHOTOS]:
            pid = it.get("product_id")
            product = None
            if pid:
                try:
                    product = await woo.get_product(pid)
                except Exception as e:
                    print(f"[pipeline] محصول {pid} گرفته نشد: {e}")

            src = (it.get("image") or {}).get("src")
            if not src and product:
                imgs = product.get("images") or []
                src = imgs[0]["src"] if imgs else None
            if src:
                jpg = await media.fetch_jpeg(src)
                if jpg:
                    photos.append(jpg)

            loc = _stock_location(product, it.get("quantity", 1))
            if loc:
                locations.append(loc)

        stock_location = "، ".join(dict.fromkeys(locations)) if locations else None
        summary = plugin_events.summarize(await _safe_notes(order_id))

        caption = telegram_io.build_caption(order, stock_location, summary)
        msg_id = await telegram_io.post_order(app, order, photos, caption)
        db.mark_posted(order_id, msg_id, config.TELEGRAM_GROUP_ID, order.get("status"), stock_location, caption)
        print(f"[pipeline] سفارش {order_id} درج شد (پیام {msg_id}، {len(photos)} عکس).")


async def rebuild_and_edit(app, order_id: int):
    """کپشن سفارش پست‌شده را بازسازی و در صورت تغییر ویرایش می‌کند."""
    message_id, chat_id, status_old, caption_old, stock_location = db.get_edit_row(order_id)
    if not message_id:  # seed یا پست‌نشده
        return
    try:
        order = await woo.get_order(order_id)
    except Exception as e:
        print(f"[edit] سفارش {order_id} گرفته نشد: {e}")
        return
    summary = plugin_events.summarize(await _safe_notes(order_id))
    caption_new = telegram_io.build_caption(order, stock_location, summary)
    if caption_new == caption_old:
        return
    try:
        await telegram_io.edit_caption(app, message_id, chat_id, caption_new)
        db.update_after_edit(order_id, order.get("status"), caption_new)
        print(f"[edit] کپشن سفارش {order_id} به‌روزرسانی شد.")
    except Exception as e:
        # «message is not modified» را بی‌صدا رد کن
        if "not modified" not in str(e).lower():
            print(f"[edit] ویرایش سفارش {order_id} ناموفق: {e}")
        else:
            db.update_after_edit(order_id, order.get("status"), caption_new)
