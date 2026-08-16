@echo off
REM جابِ روزانهٔ سینکِ قیمت/موجودی (کانالِ CAT Group → سایت). توسطِ Scheduled Task «WooPriceSync» اجرا می‌شود.
REM فلگ‌ها اینجا (نه در .envِ محرمانه) تنظیم می‌شوند؛ load_dotenv این‌ها را override نمی‌کند.
REM خاموش‌کردنِ کلِ سینک: این تسک را Disable کن. فقط‌گزارش (بدونِ نوشتن): WT_PRICESYNC_APPLY=0
cd /d C:\A2\woo-orderbot
set PYTHONUTF8=1
set WT_PRICESYNC_ENABLED=1
set WT_PRICESYNC_APPLY=1
".venv\Scripts\python.exe" -u pricesync_sync.py
