"""تستِ واحدِ منطقِ خالص (بدونِ نیاز به pytest؛ با `python tests/test_logic.py` هم اجرا می‌شود).

پوشش: تشخیصِ مرخصی/تعطیل، برچسبِ فارسیِ منبع، نرمال‌سازیِ موبایل، تفکیکِ تخفیف در کپشن، رقمِ فارسی.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(name, cond):
    print(("✅ " if cond else "❌ ") + name)
    return bool(cond)


def test_leave_kind():
    import worktasks as w
    r = True
    r &= check("مرخصی → leave", w._leave_kind("مرخصی") == "leave")
    r &= check("مرخصیم → leave", w._leave_kind("مرخصیم") == "leave")
    r &= check("تعطیل → holiday", w._leave_kind("تعطیل") == "holiday")
    r &= check("off → holiday", w._leave_kind("off") == "holiday")
    r &= check("گزارشِ بلند → None", w._leave_kind("امروز ۱۰۰ محصول را دسته‌بندی و قیمت‌گذاری کردم") is None)
    r &= check("خالی → None", w._leave_kind("") is None)
    return r


def test_source_label():
    import crm
    r = True
    r &= check("webchat → چت سایت", crm.source_label("webchat") == "چت سایت")
    r &= check("inperson_form → مشتری حضوری", crm.source_label("inperson_form") == "مشتری حضوری")
    r &= check("website_popup → پاپ‌آپ سایت", crm.source_label("website_popup") == "پاپ‌آپ سایت")
    r &= check("ناشناخته → خام", crm.source_label("xyz_new") == "xyz_new")
    r &= check("normalize +98", crm.normalize_phone("+989120000000") == "09120000000")
    r &= check("normalize 0098", crm.normalize_phone("0098 912 000 0000") == "09120000000")
    return r


def test_caption_discount():
    import telegram_io
    order = {
        "number": "12345", "id": 12345, "status": "processing", "date_created": "2026-07-10T10:00:00",
        "billing": {"first_name": "علی", "last_name": "رضایی", "phone": "09120000000",
                    "state": "THR", "address_1": "خ آزادی", "city": "تهران", "postcode": "1234567890"},
        "shipping": {}, "payment_method_title": "درگاه", "shipping_lines": [{"method_title": "پیک"}],
        "line_items": [{"name": "ساعت", "subtotal": "2000000", "total": "1700000", "quantity": 1}],
        "coupon_lines": [{"code": "OFF15"}], "discount_total": "300000",
        "shipping_total": "50000", "total": "1750000",
    }
    cap = telegram_io.build_caption(order)
    r = True
    r &= check("تخفیف‌دار: «قیمت قبل تخفیف» هست", "قیمت قبل تخفیف" in cap)
    r &= check("تخفیف‌دار: کوپن OFF15 هست", "OFF15" in cap)
    r &= check("تخفیف‌دار: «هزینه ارسال» هست", "هزینه ارسال" in cap)
    r &= check("تخفیف‌دار: «مبلغ پرداختی» هست", "مبلغ پرداختی" in cap)
    order2 = dict(order)
    order2["discount_total"] = "0"
    order2["coupon_lines"] = []
    cap2 = telegram_io.build_caption(order2)
    r &= check("بدونِ تخفیف: خطِ «قیمت قبل تخفیف» نیست", "قیمت قبل تخفیف" not in cap2)
    r &= check("بدونِ تخفیف: «مبلغ پرداختی» هست", "مبلغ پرداختی" in cap2)
    return r


def test_fa():
    import worktasks as w
    return check("_fa 123 → ۱۲۳", w._fa(123) == "۱۲۳")


if __name__ == "__main__":
    tests = [test_leave_kind, test_source_label, test_caption_discount, test_fa]
    results = []
    for t in tests:
        print(f"\n— {t.__name__} —")
        results.append(t())
    print(f"\n{'✅' if all(results) else '❌'} {sum(results)}/{len(results)} گروهِ تست پاس شد")
    sys.exit(0 if all(results) else 1)
