# woo-orderbot

A Telegram bot that turns WooCommerce orders into **live, self-updating order
cards** in a group chat — with an interactive sales dashboard, automated
abandoned-order recovery, and full management reporting for admins.

For every paid order it posts a rich card — featured product image plus full
order details with a Jalali (Shamsi) date. That card is *live*: as the order
changes (status transitions, product swaps, price corrections, balance
payments) the bot edits the same message in place instead of sending reply
spam. Admins get an inline-keyboard menu for sales and management reports over
Shamsi date ranges, plus order search. Failed and cancelled orders are routed
to a dedicated follow-up group so the sales team can win them back, with every
follow-up action attributed to the agent who took it.

---

## Features

### Live order cards
- **Edited in place** — captions update on every change; no reply spam.
- **Featured product image** — fetched from the REST API and converted to JPEG; nothing is persisted to disk.
- **Plugin-aware** — parses order-edit plugin notes (product swap, price fix, balance payment, refund) into a clean summary and reflects the precise stock location.
- **Jalali (Shamsi) calendar** — order dates and every report range use the Persian calendar.

### Admin dashboard (inline keyboard)
- **Quick sales** — today / this week / this month / this year.
- **Month picker** — per-gateway revenue for any Shamsi month, with totals and percentage shares.
- **Management reports** — executive overview, multi-month trend (with bar charts), key stats (AOV, abandonment rate), top customers, top products, province breakdown, payment-gateway success/failure performance, and pending fulfillment.
- **CSV export** — one tap exports a month's paid orders.
- **Order search** — by phone, customer name, or part of a product name; each match is sent as a full card.

### Abandoned-order recovery
- **Dedicated follow-up group** — failed (abandoned) and cancelled orders are pushed to a separate group, in real time during business hours and as a daily 10:00 digest.
- **Agent attribution** — every lead carries three action buttons (contacted / no answer / bought); each tap is recorded against the agent who pressed it, and an outcomes report summarises performance by agent.
- **One-tap contact** — a button opens a Telegram chat with the customer's phone number.

### Operations
- **Daily summary** — a sales recap is posted at local midnight (Tehran).
- **Accurate clock** — local time is derived from an external time source, so scheduled jobs fire correctly even when the host clock has drifted.
- **Warm report cache** — reports are pre-warmed in the background so the admin menu responds instantly.
- **Self-healing service** — runs as a Windows scheduled task with start-on-boot and crash recovery; a single long-polling instance with explicit update delivery.

## Architecture

```
WooCommerce (REST + optional Webhook)
        │
        ▼
   poller / webhook ──► pipeline ──► Telegram group
        │                  │
        │                  ├─ featured image (Pillow)
        │                  ├─ caption (province, stock, plugin edits)
        │                  └─ in-place caption editing
        │
        ├─ reports ──► admin dashboard (inline keyboard)
        └─ leads   ──► follow-up group (agent-attributed buttons)
        ▼
     SQLite (order ↔ message map, status, caption, leads, outcomes)
```

The bot runs wherever Telegram is reachable; the WooCommerce store can live
elsewhere (e.g. behind a region block) and acts purely as the data source.

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
> Use `virtualenv` rather than the stdlib `venv` so the interpreter receives
> its arguments correctly under Task Scheduler.

## Configuration (`.env`)

| Key | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_GROUP_ID` | Destination group id for order cards |
| `FOLLOWUP_GROUP_ID` | Group id for abandoned/cancelled lead follow-up |
| `ADMIN_USER_IDS` | Comma-separated admin user ids (reports & search) |
| `WOO_URL` / `WOO_CK` / `WOO_CS` | WooCommerce REST URL and keys |
| `POST_STATUSES` | Order statuses to post (e.g. `processing,completed,delivered`) |
| `PAID_STATUSES` | Statuses counted as revenue in reports |
| `MONEY_DIVISOR` | Currency divisor (Rial→Toman = `10`) |
| `POLL_INTERVAL_SECONDS` | Polling interval |
| `WEBHOOK_ENABLED` | Enable the instant webhook (optional) |

## Admin menu & commands

The admin menu (`/start` or `/menu`) is an inline keyboard covering quick sales,
a Shamsi month picker, the full **Analytics & reports** submenu, lead follow-up,
agent outcomes, CSV export, and order search.

- `/menu` — open the dashboard
- `/range ۱۴۰۳/۰۱/۰۱ ۱۴۰۳/۰۱/۳۱` — custom-range report
- `/setfollowup` — (sent inside the follow-up group) registers it as the lead group

## Tech stack

Python · python-telegram-bot · WooCommerce REST API · Pillow · jdatetime ·
FastAPI/Uvicorn (optional webhook) · SQLite

---

All sensitive data lives in `.env` and is kept out of version control.
