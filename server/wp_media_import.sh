#!/bin/bash
# wp_media_import.sh — واردکردنِ عکس به کتابخانهٔ رسانهٔ وردپرس با نامِ کنترل‌شده (imgpipe).
# مستقر در: /home/siteuser/wp_media_import.sh   (مالک: siteuser)
# اجرا:  su -s /bin/bash siteuser -c "/home/siteuser/wp_media_import.sh"  < lines.tsv
#
# ورودی stdin (تب‌جدا):  filename <TAB> source
#    filename : نامِ نهاییِ دقیقِ فایل در کتابخانهٔ رسانه، مثلِ  16-6018-13-007.jpg  یا  16-6018-13-007-1.jpg
#               (نام عیناً حفظ می‌شود — هیچ بازسازی/حدسی روی رفرنس انجام نمی‌شود؛ رفرنس‌ها خودشان خط‌تیره دارند.)
#    source   : URLِ عکسِ مبدأ، یا مسیرِ فایلِ محلیِ روی همین سرور (خروجیِ فوتوشاپ)
# خروجی      :  OK <TAB> filename <TAB> attachment_id     یا   ERR <TAB> filename <TAB> reason
#
# قرارداد (همان که هم اسکریپتِ فوتوشاپ می‌سازد هم mediaimg.find_media می‌خواند):
#   {ref}.jpg = عکسِ شاخص  ·  {ref}-1.jpg، {ref}-2.jpg، … = گالری به‌ترتیبِ شماره.
# هیچ محصولی اینجا تغییر نمی‌کند؛ فقط رسانه وارد می‌شود. اتصال به محصول را سمتِ پایتون انجام می‌دهیم.
set -o pipefail
WP_PATH="/home/siteuser/public_html"
WP_BIN="$(command -v wp || echo /usr/local/bin/wp)"
TMP="$(mktemp -d /tmp/imgpipe.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

ok=0; err=0
while IFS=$'\t' read -r fname src; do
  fname="$(echo "$fname" | tr -d '\r' | xargs)"
  src="$(echo "$src" | tr -d '\r' | xargs)"
  [ -z "$fname" ] && continue
  fname="$(basename "$fname")"            # ضدِ path traversal
  case "$fname" in *..*|"") echo -e "ERR\t$fname\tbad_filename"; err=$((err+1)); continue ;; esac
  target="$TMP/$fname"

  case "$src" in
    http://*|https://*)
      if ! curl -sL --max-time 90 --retry 2 -o "$target" "$src"; then
        echo -e "ERR\t$fname\tdownload_failed"; err=$((err+1)); continue
      fi
      ;;
    *)
      [ -f "$src" ] || { echo -e "ERR\t$fname\tfile_not_found"; err=$((err+1)); continue; }
      cp -f "$src" "$target" || { echo -e "ERR\t$fname\tcopy_failed"; err=$((err+1)); continue; }
      ;;
  esac

  # اعتبارِ حداقلی: فایلِ خالی/خیلی کوچک وارد نشود
  sz=$(stat -c%s "$target" 2>/dev/null || echo 0)
  if [ "$sz" -lt 2000 ]; then
    echo -e "ERR\t$fname\ttoo_small_${sz}b"; err=$((err+1)); rm -f "$target"; continue
  fi

  aid="$("$WP_BIN" --path="$WP_PATH" media import "$target" --porcelain 2>/dev/null | tail -1)"
  if echo "$aid" | grep -qE '^[0-9]+$'; then
    echo -e "OK\t$fname\t$aid"; ok=$((ok+1))
  else
    echo -e "ERR\t$fname\twp_import_failed"; err=$((err+1))
  fi
  rm -f "$target"
done

echo "done ok=$ok err=$err" >&2
