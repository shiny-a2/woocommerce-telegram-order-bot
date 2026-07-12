"""مغزِ اختصاصیِ ارزیابیِ عملکرد (OpenAI gpt-5.5) — فقط برای ماژولِ گزارشِ کار.

از همان کلیدِ OpenAI (config.OPENAI_API_KEY) با مدلِ config.WT_MODEL استفاده می‌کند.
اگر کلید نباشد یا خطا بدهد، fail-soft است (رشته/دیکشنریِ خالی) و ماژول بی‌AI کار می‌کند.
"""
from __future__ import annotations

import json

import config

_client = None


def enabled() -> bool:
    return bool(config.OPENAI_API_KEY)


def _client_():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


async def _chat(system: str, user: str, max_tokens: int) -> str:
    m = config.WT_MODEL
    kwargs = {
        "model": m,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if m.startswith(("gpt-5", "o1", "o3", "o4")):  # مدل‌های استدلالی: temperature نمی‌گیرند
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = 0.4
        kwargs["max_tokens"] = max_tokens
    r = await _client_().chat.completions.create(**kwargs)
    return (r.choices[0].message.content or "").strip()


async def followup_questions(name: str, done: str, opent: str, report: str, store: str = "", directives: str = "") -> str:
    """۲ تا ۴ سؤالِ دقیق برای روشن‌شدنِ کارِ امروز، «کارِ مانده» و مسیرِ رشد."""
    if not enabled():
        return ""
    system = (
        "تو «مدیرِ داخلیِ ریزبینِ» یک فروشگاهِ ووکامرسِ ایرانی هستی؛ منصف و محترم، اما بسیار دقیق و موشکاف، و هدفِ نهایی‌ات "
        "رشد و ارتقای فروش است. می‌خواهی هیچ ابهامی درباره‌ی کارِ امروز، «کارِ مانده» و مسیرِ رشد باقی نماند. با لحنِ محترمانه "
        "ولی جدی، ۲ تا ۴ سؤالِ کوتاه و مشخص بپرس که: "
        "(۱) عددِ کل و باقی‌مانده را دقیق روشن کند (مثلاً «۱۰۰ محصول دسته شد» → «کلاً چند تا بود؟ چند تا ماند؟»)؛ "
        "(۲) موارِد جانبیِ ناتمام را دربیاورد (قیمت/عکس/توضیحات/موجودی/سئو هرکدام چه شد)؛ "
        "(۳) اگر بخشِ «🔁 راستی‌آزماییِ کارِ مانده» داده شده، برای هر آیتمِ مانده‌ی قبلی که کارمند به آن اشاره نکرده، صریح بپرس "
        "«انجام شد یا نه، و اگر نه چرا و دقیقاً کِی تمام می‌شود»؛ "
        "(۴) اگر تسکی نشانِ «⏳عقب‌افتاده» دارد و به آن اشاره نشده، حتماً دلیل و مهلتِ دقیقِ اتمام را بپرس؛ "
        "(۵) اگر «داده‌ی واقعی» (آمارِ فروشگاه، لاگِ سایت، آنالیزِ اینستاگرام) با ادعای کارمند نمی‌خوانَد، محترمانه همان مغایرت را بپرس؛ "
        "(۶) در صورتِ امکان یک سؤالِ رو‌به‌جلو بپرس که کارمند را به فکرِ فروشِ بیشتر بیندازد "
        "(مثلاً «کدام محصول امروز بیشترین بازدید/سؤال را داشت و برایش چه کردی؟»). "
        "اگر در ورودی بخشی با عنوانِ «🔴 دستورهای مدیر (اولویتِ مطلق)» آمد، آن دستورها بر همه‌ی قواعدِ بالا مقدم‌اند: "
        "تمرکز، لحن و انتخابِ سؤال‌هایت باید کاملاً با آن‌ها هم‌راستا باشد. "
        "فقط سؤال‌ها را فارسی و شماره‌دار بنویس، بدونِ مقدمه و نتیجه‌گیری."
    )
    user = f"کارمند: {name}\nتسک‌های انجام‌شده‌ی امروز: {done}\nتسک‌های باز: {opent}\nگزارشِ خودش: {report}"
    if store:
        user += f"\n{store}"
    if directives:
        user = directives + "\n\n" + user
    try:
        return await _chat(system, user, 450)
    except Exception as e:
        print(f"[wt_brain] followup_questions خطا: {e!r}")
        return ""


async def evaluate(name: str, done: str, opent: str, report: str, qa: str, store: str = "", directives: str = "") -> dict:
    """ارزیابیِ رشدمحور + راستی‌آزماییِ کارِ مانده + تسک‌سازیِ SMART.

    خروجی: {score, summary, carryover[], remaining[], blockers[], tasks[], growth_tips[], flags[]}.
    - tasks = list[dict] با {text, priority, kind, metric, label}.
    - carryover = list[dict] با {item, status, detail, recurring}.
    """
    if not enabled():
        return {}
    system = (
        "تو «مدیرِ داخلیِ ریزبینِ رشدمحورِ» یک فروشگاهِ ووکامرسِ ایرانی هستی. هدفِ نهایی‌ات رشد و ارتقای فروش است. منصف، دقیق و "
        "انگیزه‌بخش هستی، اما روی عدد و کارِ مانده سخت‌گیری. کارت سه بخش است:\n"
        "الف) راستی‌آزماییِ کارِ مانده: از بخشِ «🔁 راستی‌آزماییِ کارِ مانده» فهرستِ کارهای مانده‌ی گزارشِ قبلی داده می‌شود. برای هر "
        "آیتم دقیقاً یکی را تعیین کن: «انجام شد»/«نیمه‌کاره»/«هنوز مانده»/«نامشخص»، با عدد و دلیلِ کوتاه. کارِ کهنه‌ای که «امروز "
        "بالاخره بسته شد» را نقطه‌ی مثبت لحاظ کن. آیتمی که چند روز پشتِ‌هم مانده را با recurring=true و یک flag پرچم بزن.\n"
        "ب) تحلیلِ عملکردِ امروز با کراس‌چکِ داده‌ی واقعی: اگر آمارِ فروشگاه/لاگِ سایت/آنالیزِ اینستاگرام داده شده، ادعاهای عددیِ "
        "کارمند را با آن‌ها بسنج و هر مغایرت یا ادعای اثبات‌نشده را در flags بیاور.\n"
        "ج) تسک‌سازیِ رشدمحور برای فردا: هر تسک SMART باشد (مشخص، قابل‌سنجش با عدد/معیار، واقع‌بینانه، مهلت‌دار) و در خدمتِ فروش. "
        "سه جنس بساز و اولویت بده: «followup» = تکمیلِ کارِ مانده‌ی راستی‌آزمایی‌شده (اگر تکرارشونده، اولویتِ بالا)؛ «sales» = حرکتِ "
        "رشدیِ فروش مرتبط با داده‌ی واقعی (مثلاً عکس‌دارکردنِ محصولاتِ پربازدیدِ بی‌عکس، پیگیریِ سبدِ رهاشده)، هرجا شد یک metric بگذار؛ "
        "«skill» = یک مهارت/آموزشِ کوچک که فروش را بلندمدت بالا می‌برد. حداکثر ۶ تسکِ بی‌تکرار، متناسبِ یک روز.\n"
        "لحن: مثلِ مدیرِ باتجربه که هم حواسش به عدد است هم آدم را می‌سازد — عادلانه، بدونِ تحقیر، با یک نکته‌ی انگیزشیِ فروش‌محور.\n"
        "اگر بخشی با عنوانِ «🔴 دستورهای مدیر (اولویتِ مطلق)» در ورودی بود، آن معیارها بر قواعدِ عمومیِ بالا مقدم‌اند و باید در "
        "نمره‌دهی، summary و tasks مو‌به‌مو رعایت شوند.\n"
        "فقط و فقط یک JSON با این کلیدها برگردان، بدونِ هیچ متنِ اضافه:\n"
        "score (عددِ صحیحِ ۰ تا ۱۰۰؛ به تکمیلِ واقعی، رفعِ عقب‌افتادگی و اثرِ فروش وزن بده)؛ "
        "summary (یک تا دو جمله‌ی عینی و عددی)؛ "
        "carryover (آرایه‌ای از {\"item\",\"status\",\"detail\",\"recurring\"}؛ status یکی از "
        "\"done\"/\"partial\"/\"open\"/\"unknown\"؛ اگر نبود، خالی)؛ "
        "remaining (آرایه‌ای از رشته‌های کوتاهِ فارسی؛ هر کارِ ناتمامِ امروز با عددِ دقیق)؛ "
        "blockers (آرایه‌ای از موانع، یا خالی)؛ "
        "tasks (آرایه‌ای از {\"text\",\"priority\",\"kind\",\"metric\"}؛ priority یکی از \"high\"/\"med\"/\"low\"؛ "
        "kind یکی از \"followup\"/\"sales\"/\"skill\"؛ metric معیارِ سنجش یا رشته‌ی خالی)؛ "
        "growth_tips (آرایه‌ای از ۱ تا ۲ نکته‌ی کوتاهِ رشد/مهارتِ فردی، یا خالی)؛ "
        "flags (آرایه‌ای از هشدارهای کوتاه برای مدیر: ناسازگاریِ عددها، ادعای اثبات‌نشده، یا عقب‌افتادگیِ تکرارشونده — یا خالی)."
    )
    user = (
        f"کارمند: {name}\nتسک‌های انجام‌شده‌ی امروز: {done}\nتسک‌های باز: {opent}\n"
        f"گزارشِ خودش: {report}\nسؤال‌وجوابِ ارزیابی: {qa}"
    )
    if store:
        user += f"\n{store}"
    if directives:
        user = directives + "\n\n" + user
    try:
        raw = (await _chat(system, user, 1100)).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw[:4].lower() == "json":
                raw = raw[4:]
        d = json.loads(raw)

        def _lst(k):
            return [str(x).strip() for x in (d.get(k) or []) if str(x).strip()]

        _PR = {"high": "🔴", "med": "🟡", "low": "🟢"}
        tasks = []
        for t in (d.get("tasks") or []):
            if isinstance(t, dict):
                txt = str(t.get("text", "")).strip()
                if not txt:
                    continue
                pr = t.get("priority", "med")
                metric = str(t.get("metric", "")).strip()
                label = f"{_PR.get(pr, '🟡')} {txt}" + (f" ({metric})" if metric else "")
                tasks.append({"text": txt, "priority": pr, "kind": t.get("kind", "sales"),
                              "metric": metric, "label": label})
            else:
                s = str(t).strip()
                if s:
                    tasks.append({"text": s, "priority": "med", "kind": "followup", "metric": "", "label": f"🟡 {s}"})

        carry = []
        for c in (d.get("carryover") or []):
            if isinstance(c, dict) and str(c.get("item", "")).strip():
                carry.append({"item": str(c.get("item")).strip(), "status": c.get("status", "unknown"),
                              "detail": str(c.get("detail", "")).strip(), "recurring": bool(c.get("recurring"))})

        return {
            "score": max(0, min(100, int(d.get("score", 0)))),
            "summary": str(d.get("summary", "")).strip(),
            "carryover": carry,
            "remaining": _lst("remaining"),
            "blockers": _lst("blockers"),
            "tasks": tasks,
            "growth_tips": _lst("growth_tips"),
            "flags": _lst("flags"),
        }
    except Exception as e:
        print(f"[wt_brain] evaluate خطا: {e!r}")
        return {}


async def interpret_manager_reply(original_bot_text: str, manager_reply: str, context: str = "") -> dict:
    """ریپلای مدیر روی پیامِ ربات را به کنشِ ساختاریافته تبدیل می‌کند.

    خروجی JSON: ack, directive, scope('global'/'user'), target_hint, tasks[], close_task_ids[], correction.
    """
    if not enabled():
        return {}
    system = (
        "تو «مغزِ فرمان‌پذیریِ» یک مدیرِ داخلیِ فروشگاهِ ایرانی هستی. مدیر روی یکی از پیام‌های ربات (سؤالِ ارزیابی، کارتِ عملکرد، "
        "یا هر پیامِ دیگر) «ریپلای» زده و منظورش را گفته. خواسته‌ی مدیر را دقیق بفهم و به کنش تبدیل کن؛ مدیر رئیس است و حرفش "
        "اولویتِ مطلق دارد.\n"
        "قواعد:\n"
        "۱) اگر مدیر یک «قاعده/سیاستِ همیشگی» گفت (مثلاً «از این به بعد به هرکس عکس آپلود نکرده نمره‌ی کامل نده»، «همیشه تعدادِ "
        "تماسِ روزانه را بپرس»، «لحنت را نرم‌تر کن»)، آن را در directive بگذار. اگر فقط دستورِ موردیِ همین‌بار بود، directive را خالی بگذار.\n"
        "۲) scope: اگر دستور/تسک درباره‌ی یک نفرِ خاص است 'user' و نامش را در target_hint بگذار؛ اگر برای کلِ تیم است 'global'.\n"
        "۳) اگر مدیر خواست کاری سپرده شود، متنِ تسکِ عملیِ فعل‌محورِ قابل‌اندازه‌گیری را در tasks بگذار (از محاوره به جمله‌ی کاریِ تمیز).\n"
        "۴) اگر مدیر به شماره‌ی تسکی اشاره کرد و گفت ببند/تمام شد/لازم نیست، آن شماره‌ها را در close_task_ids بگذار.\n"
        "۵) اگر مدیر گفت یک تسکِ موجود اشتباه/ناقص است و باید اصلاح شود، در edits یک آیتمِ {\"task_id\", \"new_text\"} بگذار؛ "
        "task_id از فهرستِ «تسک‌های بازِ ...»ِ اطلاعاتِ کمکی یا شماره‌ای که مدیر گفت، و new_text = متنِ کاملِ «اصلاح‌شده‌ی» تسک "
        "(اصلِ تسک را با تغییرِ خواسته‌ی مدیر ادغام کن، نه فقط بخشِ عوض‌شده). اگر شماره‌ی تسک قابلِ‌تشخیص نبود، به‌جای حدس، edits را خالی بگذار.\n"
        "۶) اگر مدیر ارزیابی/برداشتِ قبلیِ ربات را اصلاح کرد (مثلاً «نه، او واقعاً کار کرده، سخت نگیر»)، خلاصه‌ی اصلاح را در correction بگذار.\n"
        "۷) ack یک تأییدِ کوتاه، محترمانه و مطمئنِ فارسی است که نشان دهد فهمیدی و چه می‌کنی — مثلِ «چشم مدیر، از این پس رعایت می‌شود». "
        "چاپلوسی نکن، شفاف باش.\n"
        "فقط و فقط یک JSON با کلیدهای: ack, directive, scope, target_hint, tasks, edits, close_task_ids, correction برگردان."
    )
    user = f"پیامِ ربات که مدیر رویش ریپلای زده:\n«{original_bot_text}»\n\nریپلای/دستورِ مدیر:\n«{manager_reply}»"
    if context:
        user += f"\n\nاطلاعاتِ کمکی:\n{context}"
    try:
        raw = (await _chat(system, user, 600)).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw[:4].lower() == "json":
                raw = raw[4:]
        d = json.loads(raw)

        def _lst(k):
            return [str(x).strip() for x in (d.get(k) or []) if str(x).strip()]

        def _ids(k):
            out = []
            for x in (d.get(k) or []):
                try:
                    out.append(int(str(x).strip().lstrip("#")))
                except (TypeError, ValueError):
                    pass
            return out

        def _edits(k):
            out = []
            for e in (d.get(k) or []):
                if not isinstance(e, dict):
                    continue
                try:
                    tid = int(str(e.get("task_id")).strip().lstrip("#"))
                except (TypeError, ValueError):
                    continue
                nt = str(e.get("new_text", "")).strip()
                if nt:
                    out.append({"task_id": tid, "new_text": nt})
            return out
        scope = "user" if str(d.get("scope", "")).strip() == "user" else "global"
        return {
            "ack": str(d.get("ack", "")).strip(),
            "directive": str(d.get("directive", "")).strip(),
            "scope": scope,
            "target_hint": str(d.get("target_hint", "")).strip(),
            "tasks": _lst("tasks"),
            "edits": _edits("edits"),
            "close_task_ids": _ids("close_task_ids"),
            "correction": str(d.get("correction", "")).strip(),
        }
    except Exception as e:
        print(f"[wt_brain] interpret_manager_reply خطا: {e!r}")
        return {}


async def route_issues(issues: list, staff: list) -> list:
    """هر مشکلِ خزش را به مناسب‌ترین پرسنل (بر اساسِ شرحِ وظایفش) نگاشت می‌کند و متنِ تسکِ تمیز می‌سازد.

    issues = [{"key","text"}] (یا رشته‌ی ساده). staff = [{"name","role"}].
    خروجی: [{"key","task_text","assignee"}] — key عیناً از ورودی تکرار می‌شود (برای dedupِ قطعی)؛
    assignee خالی = نامشخص (تسکِ بی‌مسئول برای اساینِ دستیِ مدیر).
    """
    if not enabled() or not issues:
        return []
    norm = [(i if isinstance(i, dict) else {"key": "", "text": str(i)}) for i in issues]
    valid_keys = {str(i.get("key") or "") for i in norm}
    roster = "\n".join(f"- {s['name']}: {s['role']}" for s in staff) or "— (هیچ پرسنلی شرحِ وظایف ندارد)"
    system = (
        "تو دستیارِ «مدیرِ داخلی» هستی. فهرستی از «مشکلاتِ پیداشده» (هرکدام با یک key) و فهرستِ پرسنل با «شرحِ وظایف»‌شان داری. "
        "برای هر مشکل: key را عیناً تکرار کن، یک متنِ تسکِ کوتاه/عملی/فعل‌محورِ فارسی بساز، و فقط اگر واقعاً در حوزه‌ی "
        "شرحِ وظایفِ یک نفر است او را assignee بگذار؛ وگرنه assignee را خالی بگذار (حدس نزن). "
        "فقط و فقط یک JSON برگردان: "
        "{\"assignments\":[{\"key\":\"همان key ورودی\",\"task_text\":\"...\",\"assignee\":\"نامِ دقیق یا رشته‌ی خالی\"}]}."
    )
    user = ("پرسنل و شرحِ وظایف:\n" + roster + "\n\nمشکلاتِ پیداشده (key | متن):\n"
            + "\n".join(f"- {i.get('key')} | {i.get('text')}" for i in norm))
    try:
        raw = (await _chat(system, user, 800)).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw[:4].lower() == "json":
                raw = raw[4:]
        d = json.loads(raw)
        out = []
        for a in (d.get("assignments") or []):
            if not isinstance(a, dict):
                continue
            txt = str(a.get("task_text", "")).strip()
            key = str(a.get("key", "")).strip()
            if key not in valid_keys:  # هذیانِ AI روی key → خالی (فالبک سمتِ فراخوان تطبیق می‌دهد)
                key = ""
            if txt:
                out.append({"key": key, "task_text": txt, "assignee": str(a.get("assignee", "")).strip()})
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[wt_brain] route_issues خطا: {e!r}")
        return []
