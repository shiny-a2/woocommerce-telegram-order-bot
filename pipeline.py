"""خط پردازش سفارش: درج سفارش جدید + ویرایش کپشنِ سفارش‌های تغییریافته.

کپشن یک پیامِ «زنده» است: با هر تغییر، کپشن همان پیام بازسازی و در صورت اختلاف
ویرایش می‌شود. تابع build_order_card هم برای درج اولیه و هم برای جستجو استفاده می‌شود.
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


async def build_order_card(order):
    """(photos, caption, stock_location) برای یک سفارش می‌سازد (عکس شاخص + کپشن کامل)."""
    photos, locations = [], []
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
    summary = plugin_events.summarize(await _safe_notes(order.get("id")))
    caption = telegram_io.build_caption(order, stock_location, summary)
    return photos, caption, stock_location


async def process_order(app, order_id: int):
    lock = await _order_lock(order_id)
    async with lock:
        if db.is_posted(order_id):
            return
        if order_id <= int(db.get_meta("baseline_id") or 0):  # محافظ خط مبنا
            return
        order = await woo.get_order(order_id)
        if not _should_post(order):
            return
        photos, caption, stock_location = await build_order_card(order)
        msg_id = await telegram_io.post_order(app, order, photos, caption)
        db.mark_posted(order_id, msg_id, config.TELEGRAM_GROUP_ID, order.get("status"), stock_location, caption)
        print(f"[pipeline] سفارش {order_id} درج شد (پیام {msg_id}، {len(photos)} عکس).")


async def _product_photo_by_name(name):
    """عکسِ اولِ محصولی که با این نام/رفرنس در فروشگاه پیدا می‌شود (برای ساعتِ تعویض‌شده)."""
    try:
        rows = await woo.get("products", {"search": name, "per_page": 1, "_fields": "id,name,images"})
    except Exception as e:  # noqa: BLE001
        print(f"[edit] جستجوی محصولِ «{name}» ناموفق: {e!r}")
        return None
    imgs = (rows[0].get("images") if rows else None) or []
    src = imgs[0].get("src") if imgs else None
    return await media.fetch_jpeg(src) if src else None


def _fa_label(canvas, x, w, text, color):
    """نوارِ رنگیِ بالای یک نیمه با متنِ فارسیِ درست (reshape+bidi) — مثلِ «قبل»/«بعد»."""
    from PIL import ImageDraw, ImageFont
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt = get_display(arabic_reshaper.reshape(text))
    except Exception:  # noqa: BLE001 — اگر کتابخانه نبود، بی‌برچسب ادامه بده
        return
    draw = ImageDraw.Draw(canvas, "RGBA")
    fs = max(20, canvas.height // 16)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\tahoma.ttf", fs)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    tb = draw.textbbox((0, 0), txt, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(6, fs // 3)
    bar_h = th + 2 * pad
    draw.rectangle([x, 0, x + w, bar_h], fill=color + (225,))
    draw.text((x + (w - tw) // 2 - tb[0], pad - tb[1]), txt, font=font, fill=(255, 255, 255))


def _compose_side_by_side(a, b):
    """دو عکسِ محصول را کنارِ هم در یک تصویر می‌چسباند، با برچسبِ «قبل» (اصلی) و «بعد» (تعویضی)."""
    from PIL import Image
    import io
    ia = Image.open(io.BytesIO(a)).convert("RGB")
    ib = Image.open(io.BytesIO(b)).convert("RGB")
    h = max(ia.height, ib.height)

    def _rz(im):
        return im if im.height == h else im.resize((max(1, round(im.width * h / im.height)), h))

    ia, ib = _rz(ia), _rz(ib)
    gap = 14
    canvas = Image.new("RGB", (ia.width + ib.width + gap, h), (255, 255, 255))
    canvas.paste(ia, (0, 0))                       # چپ: ساعتِ اصلی
    canvas.paste(ib, (ia.width + gap, 0))          # راست: ساعتِ تعویضی
    _fa_label(canvas, 0, ia.width, "قبل", (110, 110, 110))
    _fa_label(canvas, ia.width + gap, ib.width, "بعد", (34, 150, 68))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()


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
    # تعویضِ ساعت: یک‌بار عکسِ «قدیمی + جدید» را کنارِ هم در همان کارت بگذار (نه فقط کپشن).
    # line_item هنوز ساعتِ قدیمی است؛ نامِ ساعتِ جدید فقط در نوتِ تعویض هست (summary["swap"][0]).
    swap = summary.get("swap")  # (نامِ جدید، نامِ قدیمی) یا None
    if swap and db.get_meta(f"photo_swapped:{order_id}") != "1":
        try:
            photos_old, cap_fresh, _loc = await build_order_card(order)  # عکسِ line_item (ساعتِ قدیمی)
            old_photo = photos_old[0] if photos_old else None
            new_photo = await _product_photo_by_name(swap[0])           # عکسِ ساعتِ جدید (از نوت)
            combined = (_compose_side_by_side(old_photo, new_photo)
                        if (old_photo and new_photo) else (new_photo or old_photo))
            if combined:
                await telegram_io.edit_media_photo(app, message_id, chat_id, combined, cap_fresh)
                db.set_meta(f"photo_swapped:{order_id}", "1")
                db.update_after_edit(order_id, order.get("status"), cap_fresh)
                n_imgs = 2 if (old_photo and new_photo) else 1
                print(f"[edit] تعویض: عکسِ {n_imgs} ساعت + کپشنِ سفارش {order_id} به‌روزرسانی شد.")
                return
        except Exception as e:  # noqa: BLE001 — افت به ویرایشِ کپشن
            print(f"[edit] آپدیتِ عکسِ تعویضِ {order_id} ناموفق: {e!r} — افت به کپشن.")
    caption_new = telegram_io.build_caption(order, stock_location, summary)
    if caption_new == caption_old:
        return
    try:
        await telegram_io.edit_caption(app, message_id, chat_id, caption_new)
        db.update_after_edit(order_id, order.get("status"), caption_new)
        print(f"[edit] کپشن سفارش {order_id} به‌روزرسانی شد.")
    except Exception as e:
        if "not modified" not in str(e).lower():
            print(f"[edit] ویرایش سفارش {order_id} ناموفق: {e}")
        else:
            db.update_after_edit(order_id, order.get("status"), caption_new)
