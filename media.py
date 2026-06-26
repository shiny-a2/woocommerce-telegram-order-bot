"""دانلود عکس شاخص محصول و تبدیل به JPEG برای ارسال در تلگرام.

تلگرام عکس را باید JPEG/PNG بگیرد؛ فروشگاه عکس‌ها را webp می‌دهد، پس تبدیل می‌کنیم.
"""
from __future__ import annotations

import asyncio
import io

import requests
from PIL import Image

_HEADERS = {"User-Agent": "Mozilla/5.0 (woo-orderbot)"}


def _download_sync(url: str) -> bytes:
    r = requests.get(url, timeout=30, headers=_HEADERS)
    r.raise_for_status()
    return r.content


async def fetch_jpeg(url: str):
    """عکس را دانلود و به بایت‌های JPEG تبدیل می‌کند؛ در صورت خطا None."""
    try:
        raw = await asyncio.to_thread(_download_sync, url)
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:
        print(f"[media] دانلود/تبدیل عکس ناموفق بود {url}: {e}")
        return None
