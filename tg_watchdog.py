"""tg_watchdog.py — نگهبانِ سلامتِ tg-outreach (Telethon).

سیگنالِ سلامت: tg-outreach هر ~۲۰ دقیقه `data/media_index.json` را می‌نویسد. اگر این فایل بیش از
STALE_MIN دقیقه کهنه باشد، یعنی کلاینتِ Telethon قطع/گیر شده (خطای «Cannot send requests while
disconnected») → تسکِ TgOutreach را ری‌استارت می‌کنیم تا دوباره وصل شود و کانالِ CAT/مدیا خوانده شود.
با کول‌داون تا حلقهٔ ری‌استارت نیفتد. توسطِ Scheduled Task «TgWatchdog» هر ۳۰ دقیقه اجرا می‌شود.
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
import urllib.request
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)
import config as c  # noqa: E402

_IDX = r"C:\A2\tg-outreach\data\media_index.json"
_LOG = os.path.join(_HERE, "data", "tg_watchdog.log")
_STAMP = os.path.join(_HERE, "data", "tg_watchdog.last")
STALE_MIN = 60          # media_index کهنه‌تر از این → گیرکرده
COOLDOWN_MIN = 40       # حداقل فاصلهٔ بینِ دو ری‌استارت
_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def log(msg: str):
    ts = datetime.datetime.now(_TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def alert(msg: str):
    token = c.TELEGRAM_BOT_TOKEN
    if not token:
        return
    for oid in (c.ADMIN_USER_IDS or []):
        try:
            b = uuid.uuid4().hex
            body = (f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{oid}\r\n"
                    f"--{b}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\n{msg}\r\n--{b}--\r\n").encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={b}"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:  # noqa: BLE001
            pass


def _cooldown_active() -> bool:
    try:
        last = float(open(_STAMP).read().strip())
        return (time.time() - last) < COOLDOWN_MIN * 60
    except Exception:  # noqa: BLE001
        return False


def _ps(cmd: str):
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True, timeout=90)


def restart_tgoutreach():
    _ps("Stop-ScheduledTask -TaskName TgOutreach -ErrorAction SilentlyContinue")
    time.sleep(5)
    _ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'tg-outreach' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    time.sleep(3)
    _ps("Start-ScheduledTask -TaskName TgOutreach")
    open(_STAMP, "w").write(str(time.time()))


def main() -> int:
    if not os.path.exists(_IDX):
        log("media_index.json نیست — tg-outreach شاید هنوز بالا نیامده. رد.")
        return 0
    age_min = (time.time() - os.path.getmtime(_IDX)) / 60
    if age_min <= STALE_MIN:
        return 0  # سالم — بی‌صدا
    if _cooldown_active():
        log(f"کهنه ({age_min:.0f}m) ولی کول‌داونِ ری‌استارت فعال است — صبر.")
        return 0
    log(f"⚠️ گیرکرده: media_index {age_min:.0f} دقیقه کهنه (> {STALE_MIN}m) → ری‌استارتِ TgOutreach.")
    restart_tgoutreach()
    log("TgOutreach ری‌استارت شد.")
    alert(f"🔄 نگهبان: سرویسِ tg-outreach گیر کرده بود (کانال {age_min:.0f} دقیقه خوانده نشده) و خودکار ری‌استارت شد. "
          f"تا چند دقیقه دیگر کانال دوباره خوانده می‌شود.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"[fatal] {type(e).__name__}: {e}")
        sys.exit(1)
