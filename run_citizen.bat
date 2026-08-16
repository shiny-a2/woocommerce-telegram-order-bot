@echo off
REM جابِ روزانهٔ سینکِ سیتیزن (app.supplier.example → سایت). توسطِ Scheduled Task «WooCitizenSync» اجرا می‌شود.
REM فلگ‌ها اینجا (نه در .envِ محرمانه). خاموش‌کردن: تسک را Disable کن. فقط‌گزارش: WT_CITIZEN_APPLY=0
cd /d C:\A2\woo-orderbot
set PYTHONUTF8=1
set WT_CITIZEN_ENABLED=1
set WT_CITIZEN_APPLY=1
".venv\Scripts\python.exe" -u citizen_job.py
