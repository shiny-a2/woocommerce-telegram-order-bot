@echo off
REM کارنامهٔ فروشِ اپراتورِ CRM → گروهِ گزارش. توسطِ Scheduled Task «WooSalesAttrib» (روزانه).
REM خاموش‌کردن: WT_SALES_ATTRIB_ENABLED=0 (یا تسک را Disable کن).
cd /d C:\A2\woo-orderbot
set PYTHONUTF8=1
set WT_SALES_ATTRIB_ENABLED=1
".venv\Scripts\python.exe" -u sales_attrib_job.py
