# woo-orderbot

A Telegram bot that turns WooCommerce orders into **live, self-updating order
cards** in a group chat, with an interactive sales dashboard for admins.

For every paid order it posts a rich card — featured product image plus full
order details with a Jalali (Shamsi) date. That card is *live*: as the order
changes (status transitions, product swaps, price corrections, balance
payments) the bot edits the same message in place instead of sending reply
spam. Admins get an inline-keyboard menu for per-gateway sales reports over
Shamsi date ranges, plus full order search.

---

## Features

- **Live order cards** — captions are edited in place on every change; no reply spam.
- **Featured product image** — fetched from the REST API and converted to JPEG; nothing is persisted to disk.
- **Jalali (Shamsi) calendar** — order dates and every sales range use the Persian calendar.
- **Dual delivery** — instant webhook plus a polling fallback, so no order is missed.
- **Interactive admin menu** — inline keyboard: today / week / month / year, a month picker, and order search.
- **Per-gateway sales reports** — revenue split by payment gateway for any Shamsi month or custom range, with totals.
- **Order search** — by phone, customer name, or part of a product name; each match is sent as a full card.
- **Plugin-aware** — parses order-edit plugin notes (product swap, price fix, balance payment) into a clean summary, and reflects the precise stock location.
- **Self-healing service** — runs as a Windows scheduled task with start-on-boot and crash recovery.

## Architecture

```
WooCommerce (REST + Webhook)
        │
        ▼
   poller / webhook ──► pipeline ──► Telegram group
        │                  │
        │                  ├─ featured image (Pillow)
        │                  ├─ caption (province, stock, plugin edits)
        │                  └─ in-place caption editing
        ▼
     SQLite (order ↔ message map, status, caption)
```

The bot runs wherever Telegram is reachable; the WooCommerce store can live
elsewhere and acts purely as the data source.

## Example order card (live output)

```
🧾 شماره سفارش: 292148
📅 تاریخ سفارش: ۱۴۰۵/۰۳/۱۷ ۲۲:۰۶
✅ وضعیت: تحویل شده

💳 روش پرداخت: …
👤 خریدار: …
📞 تماس: …
📍 استان: خوزستان
🏠 آدرس: …
📮 کدپستی: …

🛍️ محصول: …
📦 موقعیت موجودی: جهانشهر

➖ اصلاحات سفارش:
🔄 تعویض : قبلی → با : جدید
💳 مبلغ پرداختی: ۹٬۶۵۰٬۰۰۰ تومان
💵 الباقی: ۱۰٬۰۰۰ تومان
🧮 جمع نهایی: ۹٬۶۶۰٬۰۰۰ تومان
```

## Quick start

Requires Python 3.11+

```bash
python -m virtualenv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then fill in the values
python get_chat_id.py       # find your group id and admin ids
python main.py
```

> On Windows, for a stable always-on service, run `main.py` directly with this
> virtualenv's Python (e.g. a Scheduled Task on the "At startup" trigger).

## Configuration (`.env`)

| Key | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_GROUP_ID` | Destination group id |
| `ADMIN_USER_IDS` | Comma-separated admin user ids (reports & search) |
| `WOO_URL` / `WOO_CK` / `WOO_CS` | WooCommerce REST URL and keys |
| `POST_STATUSES` | Order statuses to post (e.g. `processing,completed,delivered`) |
| `MONEY_DIVISOR` | Currency divisor (Rial→Toman = `10`) |
| `POLL_INTERVAL_SECONDS` | Polling interval |
| `WEBHOOK_ENABLED` | Enable the instant webhook (optional) |

## Admin menu & commands

The admin menu (`/start` or `/menu`) is an inline keyboard:

- **Today / This week / This month / This year** — quick sales reports
- **Pick a month** — per-gateway revenue for any Shamsi month, with total
- **Search order** — type a phone, name, or product keyword to get matching cards

`/range ۱۴۰۳/۰۱/۰۱ ۱۴۰۳/۰۱/۳۱` returns a custom-range report.

## Tech stack

Python · python-telegram-bot · WooCommerce REST API · Pillow · jdatetime ·
FastAPI/Uvicorn (webhook) · SQLite

---

All sensitive data lives in `.env` and is kept out of version control.
