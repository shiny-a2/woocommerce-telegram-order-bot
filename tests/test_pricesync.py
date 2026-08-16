"""تستِ آفلاینِ همگام‌سازیِ قیمت/موجودی (wt_pricesync) — parse + قواعد. بدونِ APIِ واقعی."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wt_pricesync as ps  # noqa: E402

_ok = True


def check(name, cond):
    global _ok
    _ok = _ok and bool(cond)
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def t_parse_caption():
    cases = {
        "AD 149 11 132                  189.900.000": ("AD.149.11.132", 189900000),
        "PWAAA0321                       419.900.000": ("PWAAA0321", 419900000),
        "PU 143 11 117              199/900/000": ("PU.143.11.117", 199900000),
        "SH 141 11  636                  219.900.000": ("SH.141.11.636", 219900000),  # فاصلهٔ دوتایی
        "PWHAA0921                                   279/900/000": ("PWHAA0921", 279900000),
        "13 149 26 226                   229.900.000": ("13.149.26.226", 229900000),
        "J001B10119                      74.900.000": ("J001B10119", 74900000),
        "PWBAA2424  1 099.900.000": ("PWBAA2424", 1099900000),   # میلیاردی با فاصله بعد از رقمِ اول
        "PWBAA2424  1.099.900.000": ("PWBAA2424", 1099900000),
        "PWBAA2424  1,099,900,000": ("PWBAA2424", 1099900000),   # کاما
        "PWBAA2424  1٬099٬900٬000": ("PWBAA2424", 1099900000),   # کامای فارسی
        "AD 149 11 132 189.900.000": ("AD.149.11.132", 189900000),  # گاردِ سقف: تک‌فاصله نباید میلیاردی شود
    }
    ok = all(ps.parse_caption(k) == v for k, v in cases.items())
    ok &= ps.parse_caption("Valentine's Day") is None
    ok &= ps.parse_caption("") is None
    ok &= ps.parse_caption("با ما همراه باشید") is None
    return check("parse_caption روی نمونه‌های واقعیِ کانال (نقطه/اسلش/فاصلهٔ دوتایی)", ok)


def t_price_and_ref():
    ok = ps.parse_price("۲۷۹٬۹۰۰٬۰۰۰") == 279900000        # ارقامِ فارسی + جداکنندهٔ فارسی
    ok &= ps.parse_price("199/900/000") == 199900000
    ok &= ps.parse_price("123") is None                     # خیلی کوچک
    ok &= ps.normalize_ref("AD 149 11  132") == "AD.149.11.132"
    ok &= ps.normalize_ref("pwaaa0321") == "PWAAA0321"
    ok &= ps.family_key("AD.149.11.132") == "AD"            # خانواده = بخشِ اول (حروف)
    ok &= ps.family_key("AK.199.21.629") == "AK"
    ok &= ps.family_key("PWAAA0321") == "PWAAA0321"         # بدونِ نقطه → خودِ رفرنس (فقط با خودش مچ)
    return check("parse_price / normalize_ref / family_key", ok)


def t_parse_json():
    data = {"messages": [
        {"type": "service", "action": "pin_message"},
        {"type": "message", "text": "AD 149 11 132   189.900.000"},
        {"type": "message", "text": [{"type": "plain", "text": "PWAAA0321   419.900.000"}]},
        {"type": "message", "text": "Valentine's Day"},
        {"type": "message", "text": ""},
    ]}
    ch, st = ps.parse_channel_json(data)
    return check("parse_channel_json (list/str text + رد کردنِ بی‌قیمت)",
                 ch == {"AD.149.11.132": 189900000, "PWAAA0321": 419900000} and st["parsed"] == 2)


def t_plan():
    # خانواده = بخشِ اول. AK تک‌قیمت (269.9M) ؛ AD چندقیمت → مبهم ؛ SH تک.
    channel = {"AK.169.21.121": 269900000, "AK.199.21.121": 269900000,
               "AD.149.11.132": 259900000, "AD.143.11.131": 379900000,
               "SH.141.11.131": 219900000}
    P = [
        {"id": 1, "brand": "کاترپیلار", "ref": "AK.169.21.121", "manage_stock": False,
         "stock_status": "outofstock", "stock_quantity": None, "regular_price": 100000000},   # exact غیرتعدادی → قیمت+موجود
        {"id": 2, "brand": "کاترپیلار", "ref": "AK.199.99.999", "manage_stock": False,
         "stock_status": "outofstock", "stock_quantity": None, "regular_price": 84900000},     # خانوادهٔ AK تک، غیرتعدادی → قیمت، موجود نشو
        {"id": 3, "brand": "کاترپیلار", "ref": "AK.159.11.111", "manage_stock": True,
         "stock_status": "instock", "stock_quantity": 2, "regular_price": 100000000},          # خانوادهٔ AK، تعدادی → فقط قیمت
        {"id": 4, "brand": "کاترپیلار", "ref": "AD.149.99.999", "manage_stock": True,
         "stock_status": "instock", "stock_quantity": 1, "regular_price": 50000000},           # AD مبهم، تعدادی → qty_review، بی‌قیمت
        {"id": 5, "brand": "کاترپیلار", "ref": "AD.143.99.999", "manage_stock": False,
         "stock_status": "instock", "stock_quantity": None, "regular_price": 60000000},        # AD مبهم، غیرتعدادی → بی‌قیمت، ناموجود
        {"id": 6, "brand": "کاترپیلار", "ref": "ZZ.111.11.111", "manage_stock": False,
         "stock_status": "instock", "stock_quantity": None, "regular_price": 70000000},        # خانواده نیست، غیرتعدادی → بی‌قیمت، ناموجود
        {"id": 7, "brand": "رولکس", "ref": "R.1", "manage_stock": False,
         "stock_status": "instock", "stock_quantity": None, "regular_price": 1000000},         # برندِ غیرمجاز → لمس‌نشود
        {"id": 8, "brand": "کاترپیلار", "ref": "SH.141.11.131", "manage_stock": False,
         "stock_status": "instock", "stock_quantity": None, "regular_price": 219900000},        # exact، هم‌قیمت، هم‌موجود → بی‌تغییر
    ]
    pl = ps.plan_changes(channel, P)
    ids = lambda k: sorted(c["id"] for c in pl[k])
    ok = check("قیمتِ دقیق: فقط P1 (AK.169.21.121)", ids("price_exact") == [1])
    ok &= check("قیمتِ خانواده: P2(غیرتعدادی)+P3(تعدادی) → همه ۲۶۹٫۹M",
                ids("price_family") == [2, 3] and all(c["new"] == 269900000 for c in pl["price_family"]))
    ok &= check("→موجود: فقط P1 (رفرنسِ عیناً دقیق)", ids("set_instock") == [1])
    ok &= check("→ناموجود: P5,P6 (غیرتعدادیِ خانواده‌ای/نامطابق)", ids("set_outofstock") == [5, 6])
    ok &= check("مبهم: P4,P5 (خانوادهٔ AD چندقیمت)", ids("ambiguous_family") == [4, 5])
    ok &= check("qty_review: فقط P4 (تعدادیِ مبهم)", ids("qty_review") == [4])
    ok &= check("موجودیِ تعدادیِ دست‌نخورده: P3,P4 → ۲", pl["untouched_qty"] == 2)
    ok &= check("امنیتِ برند: رولکس(P7) در هیچ تغییری نیست",
                all(7 not in ids(k) for k in ("price_exact", "price_family", "set_instock", "set_outofstock")))
    ok &= check("P8 (SH دقیق، هم‌قیمت، موجود) → بی‌تغییر",
                all(8 not in ids(k) for k in ("price_exact", "price_family", "set_instock", "set_outofstock")))
    return ok


def main():
    tests = [t_parse_caption, t_price_and_ref, t_parse_json, t_plan]
    res = [bool(t()) for t in tests]
    p, n = sum(res), len(res)
    print(f"\n{p}/{n} گروهِ تستِ pricesync سبز شد؛ همهٔ assertها: {'✅' if _ok else '❌'}")
    sys.exit(0 if (p == n and _ok) else 1)


if __name__ == "__main__":
    main()
