#!/bin/bash
# wp_media_seo.sh — درجِ متادیتای سئو با wp-cli (کلیدِ ووکامرس اجازهٔ نوشتنِ wp/v2/media را ندارد).
#
# مستقر در: /home/siteuser/wp_media_seo.sh   (مالک: siteuser)
# اجرا:  su -s /bin/bash siteuser -c "/home/siteuser/wp_media_seo.sh media"    < lines.tsv
#        su -s /bin/bash siteuser -c "/home/siteuser/wp_media_seo.sh product"  < lines.tsv
#
# حالتِ media   — stdin (تب‌جدا):  attachment_id <TAB> alt <TAB> title <TAB> caption <TAB> description
#   alt         → پستِ‌متای _wp_attachment_image_alt  (همانی که گوگل و اسکرین‌ریدر می‌خوانند)
#   title       → post_title    · caption → post_excerpt · description → post_content
#   فیلدِ خالی نادیده گرفته می‌شود (هیچ‌وقت مقدارِ موجود با رشتهٔ خالی پاک نمی‌شود).
#
# حالتِ product — stdin (تب‌جدا):  product_id <TAB> seo_title <TAB> seo_description <TAB> focus_keyword
#   روی Rank Math نوشته می‌شود: rank_math_title / rank_math_description / rank_math_focus_keyword
#   ⚠️ فقط متای سئو؛ عنوان/قیمت/موجودیِ محصول هرگز اینجا تغییر نمی‌کند.
#
# خروجی: OK <TAB> id   یا   ERR <TAB> id <TAB> reason
set -o pipefail
WP_PATH="/home/siteuser/public_html"
WP_BIN="$(command -v wp || echo /usr/local/bin/wp)"
MODE="${1:-}"

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
        wpx post meta update "$id" _wp_attachment_image_alt "$alt" >/dev/null || failed=1
      fi
      args=()
      [ -n "$title" ]       && args+=("--post_title=$title")
      [ -n "$caption" ]     && args+=("--post_excerpt=$caption")
      [ -n "$description" ] && args+=("--post_content=$description")
      if [ "${#args[@]}" -gt 0 ]; then
        wpx post update "$id" "${args[@]}" >/dev/null || failed=1
      fi
      if [ "$failed" -eq 0 ]; then echo -e "OK\t$id"; ok=$((ok+1))
      else echo -e "ERR\t$id\twp_write_failed"; err=$((err+1)); fi
    done
    echo "done mode=media ok=$ok err=$err" >&2
    ;;

  product)
    ok=0; err=0
    while IFS=$'\t' read -r id seo_title seo_desc focus; do
      id="$(printf '%s' "$id" | tr -dc '0-9')"
      [ -z "$id" ] && continue
      if [ "$(wpx post get "$id" --field=post_type)" != "product" ]; then
        echo -e "ERR\t$id\tnot_product"; err=$((err+1)); continue
      fi
      failed=0
      [ -n "$seo_title" ] && { wpx post meta update "$id" rank_math_title "$seo_title" >/dev/null || failed=1; }
      [ -n "$seo_desc" ]  && { wpx post meta update "$id" rank_math_description "$seo_desc" >/dev/null || failed=1; }
      [ -n "$focus" ]     && { wpx post meta update "$id" rank_math_focus_keyword "$focus" >/dev/null || failed=1; }
      if [ "$failed" -eq 0 ]; then echo -e "OK\t$id"; ok=$((ok+1))
      else echo -e "ERR\t$id\twp_write_failed"; err=$((err+1)); fi
    done
    echo "done mode=product ok=$ok err=$err" >&2
    ;;

  *)
    echo "usage: wp_media_seo.sh {media|product}  < lines.tsv" >&2
    exit 2
    ;;
esac
