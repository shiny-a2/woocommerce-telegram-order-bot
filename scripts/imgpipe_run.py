"""imgpipe_run.py — رانِر خطِ لولهٔ عکس: staging → کتابخانهٔ رسانه → اتصال به محصول → سئو.

نیمهٔ سرورِ کار. نیمهٔ دیگر (ادیتِ فوتوشاپ) روی ایستگاهِ طراحی است — پوشهٔ psagent/.

  python scripts/imgpipe_run.py batches
  python scripts/imgpipe_run.py import --batch 20260909T101500Z-1234 [--apply]
  python scripts/imgpipe_run.py attach --brand 2982 [--apply]
  python scripts/imgpipe_run.py full   --brand 2982 [--apply]

پیش‌فرض همیشه dry-run است؛ بدونِ --apply هیچ چیزی روی سایت نوشته نمی‌شود.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import imgpipe  # noqa: E402
import imgseo  # noqa: E402


def _p(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def cmd_batches(_args) -> int:
    rows = imgpipe.list_batches()
    if not rows:
        print("هیچ بستهٔ ادیت‌شده‌ای روی سرور نیست.")
        return 0
    for r in rows:
        print(f"{r['batch']}\t{r['files']} فایل")
    return 0


def cmd_import(args) -> int:
    batches = [args.batch] if args.batch else [b["batch"] for b in imgpipe.list_batches()]
    if not batches:
        print("بسته‌ای برای ورود نیست.")
        return 0
    total = {"ok": 0, "err": 0, "rows": []}
    for b in batches:
        res = imgpipe.import_batch(b, apply=args.apply)
        print(f"— بستهٔ {b}: " + (f"dry-run، {res.get('would_import', 0)} فایل"
                                  if res.get("dry_run") else
                                  f"وارد شد ok={res['ok']} err={res['err']}"))
        total["ok"] += res.get("ok", 0)
        total["err"] += res.get("err", 0)
        total["rows"].extend(res.get("rows", []))
    _p({"ok": total["ok"], "err": total["err"], "sample": total["rows"][:5]})
    return 0


def cmd_attach(args) -> int:
    products = imgpipe.targets(args.brand, limit=args.limit)
    print(f"{len(products)} محصولِ بی‌عکس در این برند.")
    rows = imgpipe.attach(products, apply=args.apply)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    _p(by_status)
    return 0


def cmd_full(args) -> int:
    """زنجیرهٔ کامل. عمداً فهرستِ محصولات را **قبلِ** اتصال می‌گیریم، چون بعد از اتصال دیگر
    «بی‌عکس» نیستند و از فهرست بیرون می‌افتند (و آن‌وقت سئو چیزی برای نوشتن ندارد)."""
    products = imgpipe.targets(args.brand, limit=args.limit)
    ref_to_product = {p["ref"]: p for p in products if p.get("ref")}
    print(f"۱) هدف: {len(products)} محصولِ بی‌عکس")

    import_rows: list[dict] = []
    for b in [x["batch"] for x in imgpipe.list_batches()]:
        res = imgpipe.import_batch(b, apply=args.apply)
        import_rows.extend(res.get("rows", []))
        print(f"۲) بستهٔ {b}: ok={res.get('ok', 0)} err={res.get('err', 0)}"
              + (" (dry-run)" if res.get("dry_run") else ""))

    att = imgpipe.attach(products, apply=args.apply)
    attached = [r for r in att if r["status"] in ("attached", "would_attach")]
    print(f"۳) اتصال: {len(attached)}/{len(att)}")

    media_items = imgseo.plan_media_from_import(import_rows, ref_to_product, brand=args.brand_name)
    mres = imgseo.apply_media_seo(media_items, apply=args.apply)
    print(f"۴) سئوی رسانه: ok={mres.get('ok', 0)} err={mres.get('err', 0)}"
          + (f" (dry-run، {mres.get('would_write', 0)} خط)" if mres.get("dry_run") else ""))

    pitems = [{"product_id": r["id"], "name": r.get("name", ""), "ref": r["ref"],
               "brand": args.brand_name} for r in attached]
    pres = imgseo.apply_product_seo(pitems, apply=args.apply)
    print(f"۵) سئوی محصول: ok={pres.get('ok', 0)} err={pres.get('err', 0)}"
          + (f" (dry-run، {pres.get('would_write', 0)} خط)" if pres.get("dry_run") else ""))

    if not args.apply:
        print("\n⚠️ dry-run بود؛ هیچ چیزی روی سایت نوشته نشد. برای اجرای واقعی --apply بده.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="خطِ لولهٔ عکسِ محصول")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("batches", help="بسته‌های رسیده از ایستگاهِ طراحی")

    pi = sub.add_parser("import", help="ورودِ بسته به کتابخانهٔ رسانه")
    pi.add_argument("--batch", default="")
    pi.add_argument("--apply", action="store_true")

    pa = sub.add_parser("attach", help="اتصالِ رسانهٔ موجود به محصول با رفرنس")
    pa.add_argument("--brand", default=None, help="ترمِ برند (attribute_term)")
    pa.add_argument("--limit", type=int, default=0)
    pa.add_argument("--apply", action="store_true")

    pf = sub.add_parser("full", help="ورود + اتصال + سئو")
    pf.add_argument("--brand", default=None)
    pf.add_argument("--brand-name", default="", help="نامِ فارسیِ برند برای متنِ سئو")
    pf.add_argument("--limit", type=int, default=0)
    pf.add_argument("--apply", action="store_true")

    args = ap.parse_args()
    return {"batches": cmd_batches, "import": cmd_import,
            "attach": cmd_attach, "full": cmd_full}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
