@echo off
REM جابِ روزانهٔ سینکِ jeweltime → جواهریان (موجود/ناموجود + تعداد؛ قیمت فقط گزارش).
REM توسطِ Scheduled Task «WooJewelSync» اجرا می‌شود. خاموش‌کردن: تسک را Disable کن.
REM فقط‌گزارش (بدونِ نوشتن): WT_JEWEL_APPLY=0
cd /d C:\A2\woo-orderbot
set PYTHONUTF8=1
set WT_JEWEL_ENABLED=1
set WT_JEWEL_APPLY=1
".venv\Scripts\python.exe" -u jewel_job.py
