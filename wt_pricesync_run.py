"""wt_pricesync_run.py — ارکستراسیونِ سینکِ قیمت/موجودی: اسنپ‌شاتِ کانال → محصولاتِ برند → plan → اکسل → (اختیاری) اعمال.

- run(woo, apply=False): dry-run (هیچ نوشتنی) یا اعمالِ واقعی روی سایت. همیشه اکسل می‌سازد.
- خواندنِ کانال از فایلِ tg-outreach/data/catgroup_prices.json (سرویسِ userbot با سشنِ زندهٔ مجموعه می‌نویسد).
"""
from __future__ import annotations

import os
import time

import wt_pricesync as ps
import wt_pricesync_report as rep

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


async def run(woo, apply: bool = False, out_dir: str | None = None, channel_path: str | None = None,
              apply_limit: int | None = None) -> dict:
    prices, meta = ps.load_channel_file(channel_path)
    products = await ps.fetch_brand_products(woo)
    plan = ps.plan_changes(prices, products)
    result = None
    if apply:
        result = await ps.apply_plan(woo, plan, limit=apply_limit)
    out_dir = out_dir or _DATA
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(out_dir, f"pricesync-{'applied' if apply else 'dryrun'}-{ts}.xlsx")
    rep.build_excel(plan, products, meta, out, applied=apply, apply_result=result)
    return {"plan": plan, "meta": meta, "products": len(products), "result": result, "xlsx": out,
            "summary": ps.summarize(plan)}
