#!/bin/bash
# img_drop_receive.sh — گیرندهٔ بستهٔ عکسِ ادیت‌شده از ایستگاهِ طراحی.
#
# این اسکریپت «تنها کارِ ممکن» با کلیدِ ایستگاهِ طراحی است. در authorized_keys کاربرِ imgdrop:
#
#   command="/usr/local/bin/img_drop_receive.sh",restrict ssh-ed25519 AAAA... imgdrop-workstation
#
# با «restrict» هیچ shell/پورت‌فورواردی/pty ای ممکن نیست؛ کلید حتی اگر لو برود بیشترین کاری که
# می‌تواند بکند ریختنِ چند jpg در پوشهٔ staging است. این کلید به وردپرس/دیتابیس دسترسی ندارد.
#
# ورودی : stdin = tar.gz از فایل‌های jpg (بدونِ پوشه)
# خروجی : یک خط خلاصه روی stdout (کلاینت همان را چاپ می‌کند)
#
# واردکردن به کتابخانهٔ رسانه اینجا انجام نمی‌شود — عمداً. مرحلهٔ بعد را سمتِ مدیریت اجرا می‌کنیم
# (wp_media_import.sh) تا نوشتن روی سایت هرگز از این کلید ممکن نباشد.
set -o pipefail
umask 077

STAGING="${IMGDROP_STAGING:-/home/imgdrop/staging}"
MAX_BYTES=$((800 * 1024 * 1024))     # سقفِ حجمِ یک بسته
MAX_FILES=4000                       # سقفِ تعدادِ فایل در یک بسته

mkdir -p "$STAGING" || { echo "ERR staging_unavailable"; exit 1; }

# اگر فضای دیسک کم است، اصلاً شروع نکن (بهتر از نیمه‌کاره ماندن).
avail_kb="$(df -Pk "$STAGING" | awk 'NR==2{print $4}')"
if [ -n "$avail_kb" ] && [ "$avail_kb" -lt 1048576 ]; then
  echo "ERR low_disk_space"; exit 1
fi

batch="$STAGING/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$batch" || { echo "ERR batch_mkdir_failed"; exit 1; }

# head -c سقفِ حجم را اعمال می‌کند؛ tar فقط استخراج می‌کند و هیچ مالکیت/مجوزی را از بسته نمی‌پذیرد.
if ! head -c "$MAX_BYTES" | tar -xz -C "$batch" \
       --no-same-owner --no-same-permissions --no-absolute-names -f - 2>/dev/null; then
  rm -rf "$batch"
  echo "ERR bad_archive"
  exit 1
fi

# --- پاک‌سازیِ سخت‌گیرانه: فقط jpg با نامِ ساده در ریشهٔ بسته باقی می‌ماند ---
find "$batch" -mindepth 2 -delete 2>/dev/null
find "$batch" -mindepth 1 -maxdepth 1 ! -type f -exec rm -rf {} + 2>/dev/null

kept=0; dropped=0
for f in "$batch"/*; do
  [ -e "$f" ] || continue
  name="$(basename "$f")"
  if ! printf '%s' "$name" | grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(jpg|jpeg)$'; then
    rm -f "$f"; dropped=$((dropped+1)); continue
  fi
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -lt 2000 ]; then
    rm -f "$f"; dropped=$((dropped+1)); continue
  fi
  # اعتبارِ نوع: باید واقعاً JPEG باشد، نه فایلی که فقط پسوندِ jpg دارد.
  if ! head -c2 "$f" | od -An -tx1 | tr -d ' \n' | grep -qi '^ffd8'; then
    rm -f "$f"; dropped=$((dropped+1)); continue
  fi
  kept=$((kept+1))
  if [ "$kept" -gt "$MAX_FILES" ]; then rm -f "$f"; kept=$((kept-1)); dropped=$((dropped+1)); fi
done

if [ "$kept" -eq 0 ]; then
  rm -rf "$batch"
  echo "ERR no_valid_files dropped=$dropped"
  exit 1
fi

chmod 750 "$batch"
echo "OK batch=$(basename "$batch") kept=$kept dropped=$dropped"
