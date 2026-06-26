"""نقطه‌ی پایانی اختیاری FastAPI برای دریافت وب‌هوک سفارش ووکامرس."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import uvicorn
from fastapi import FastAPI, Request, Response

import config
import pipeline


def build_app(tg_app):
    api = FastAPI()

    @api.get("/health")
    async def health():
        return {"ok": True}

    @api.post(config.WEBHOOK_PATH)
    async def woo_order(request: Request):
        body = await request.body()

        # بررسی امضا (اگر سکرت تنظیم شده باشد)
        if config.WOO_WEBHOOK_SECRET:
            sig = request.headers.get("x-wc-webhook-signature", "")
            expected = base64.b64encode(
                hmac.new(config.WOO_WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
            ).decode()
            if not hmac.compare_digest(sig, expected):
                return Response(status_code=401, content="bad signature")

        if not body:
            return {"ok": True}  # پینگ اولیه‌ی ووکامرس
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {"ok": True}

        order_id = payload.get("id")
        if order_id:
            asyncio.create_task(pipeline.process_order(tg_app, order_id))
        return {"ok": True}

    return api


async def serve(tg_app):
    api = build_app(tg_app)
    cfg = uvicorn.Config(
        api, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT, log_level="warning"
    )
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None  # داخل لوپ مشترک اجرا می‌شود
    print(f"[webhook] فعال روی {config.WEBHOOK_HOST}:{config.WEBHOOK_PORT}{config.WEBHOOK_PATH}")
    await server.serve()
