#!/bin/bash
# wp_media_seo.sh — درجِ متادیتای سئو با wp-cli (کلیدِ ووکامرس اجازهٔ نوشتنِ wp/v2/media را ندارد).
#
# مستقر در: /home/siteuser/wp_media_seo.sh   (مالک: siteuser)
# اجرا:  su -s /bin/bash siteuser -c "/home/siteuser/wp_media_seo.sh media [--only-empty]" < lines.tsv
#        su -s /bin/bash siteuser -c "/home/siteuser/wp_media_seo.sh product [--force]"    < lines.tsv
#
# حالتِ media   — stdin (تب‌جدا):  attachment_id <TAB> alt <TAB> title <TAB> caption <TAB> description
#   alt         → پستِ‌متای _wp_attachment_image_alt  (همانی که گوگل و اسکرین‌ریدر می‌خوانند)
#   title       → post_title    · caption → post_excerpt · description → post_content
#   فیلدِ خالی نادیده گرفته می‌شود (هیچ‌وقت مقدارِ موجود با رشتهٔ خالی پاک نمی‌شود).
#   پیش‌فرض می‌نویسد، چون این حالت روی رسانهٔ تازه‌آپلودشدهٔ خودمان اجرا می‌شود.
#   --only-empty : فیلدی که از قبل مقدار دارد دست نخورَد.
#
# حالتِ product — stdin (تب‌جدا):  product_id <TAB> seo_title <TAB> seo_description <TAB> focus_keyword
#   روی Rank Math نوشته می‌شود: rank_math_title / rank_math_description / rank_math_focus_keyword
#   ⚠️ پیش‌فرض **بازنویسی نمی‌کند**: بیشترِ محصولاتِ سایت از قبل متای سئوی دستی دارند و پاک‌کردنشان
#      یک فاجعهٔ برگشت‌ناپذیر است. فقط با --force مقدارِ موجود عوض می‌شود.
#   ⚠️ فقط متای سئو؛ عنوان/قیمت/موجودیِ محصول هرگز اینجا تغییر نمی‌کند.
#
# خروجی: OK <TAB> id   ·   SKIP <TAB> id <TAB> reason   ·   ERR <TAB> id <TAB> reason
set -o pipefail
WP_PATH="/home/siteuser/public_html"
WP_BIN="$(command -v wp || echo /usr/local/bin/wp)"
MODE="${1:-}"
FLAG="${2:-}"

wpx() { "$WP_BIN" --path="$WP_PATH" "$@" 2>/dev/null; }

case "$MODE" in
  media)
    ok=0; err=0
    while IFS=$'\t' read -r id alt title caption description; do
      id="$(printf '%s' "$id" | tr -dc '0-9')"
      [ -z "$id" ] && continue
      if [ "$(wpx post get "$id" --field=post_type)" != "attachment" ]; then
        echo -e "ERR\t$id\tnot_attachment"; err=$((err+1)); continue
      fi
      failed=0
      if [ -n "$alt" ]; then
        cur=""
        [ "$FLAG" = "--only-empty" ] && cur="$(wpx post meta get "$id" _wp_attachment_image_alt)"
        if [ -z "$cur" ]; then
          wpx post meta update "$id" _wp_attachment_image_alt "$alt" >/dev/null || failed=1
        fi
      fi
      args=()
      if [ "$FLAG" = "--only-empty" ]; then
        [ -n "$title" ]       && [ -z "$(wpx post get "$id" --field=post_title)"   ] && args+=("--post_title=$title")
        [ -n "$caption" ]     && [ -z "$(wpx post get "$id" --field=post_excerpt)" ] && args+=("--post_excerpt=$caption")
        [ -n "$description" ] && [ -z "$(wpx post get "$id" --field=post_content)" ] && args+=("--post_content=$description")
      else
        [ -n "$title" ]       && args+=("--post_title=$title")
        [ -n "$caption" ]     && args+=("--post_excerpt=$caption")
        [ -n "$description" ] && args+=("--post_content=$description")
      fi
      if [ "${#args[@]}" -gt 0 ]; then
        wpx post update "$id" "${args[@]}" >/dev/null || failed=1
      fi
      if [ "$failed" -eq 0 ]; then echo -e "OK\t$id"; ok=$((ok+1))
      else echo -e "ERR\t$id\twp_write_failed"; err=$((err+1)); fi
    done
    echo "done mode=media ok=$ok err=$err" >&2
    ;;

  product)
    ok=0; err=0; skip=0
    while IFS=$'\t' read -r id seo_title seo_desc focus; do
      id="$(printf '%s' "$id" | tr -dc '0-9')"
      [ -z "$id" ] && continue
      if [ "$(wpx post get "$id" --field=post_type)" != "product" ]; then
        echo -e "ERR\t$id\tnot_product"; err=$((err+1)); continue
      fi
      failed=0; wrote=0; kept=0
      # هر فیلد جداگانه سنجیده می‌شود: محصولی که عنوانِ سئوی دستی دارد ولی کلیدواژهٔ کانونی ندارد،
      # عنوانش می‌ماند و فقط کلیدواژه پر می‌شود.
      for pair in "rank_math_title|$seo_title" "rank_math_description|$seo_desc" "rank_math_focus_keyword|$focus"; do
        key="${pair%%|*}"; val="${pair#*|}"
        [ -z "$val" ] && continue
        if [ "$FLAG" != "--force" ] && [ -n "$(wpx post meta get "$id" "$key")" ]; then
          kept=$((kept+1)); continue
        fi
        wpx post meta update "$id" "$key" "$val" >/dev/null || failed=1
        wrote=$((wrote+1))
      done
      if [ "$failed" -ne 0 ]; then echo -e "ERR\t$id\twp_write_failed"; err=$((err+1))
      elif [ "$wrote" -eq 0 ]; then echo -e "SKIP\t$id\talready_has_seo"; skip=$((skip+1))
      else echo -e "OK\t$id"; ok=$((ok+1)); fi
    done
    echo "done mode=product ok=$ok skip=$skip err=$err" >&2
    ;;

  *)
    echo "usage: wp_media_seo.sh {media|product}  < lines.tsv" >&2
    exit 2
    ;;
esac
