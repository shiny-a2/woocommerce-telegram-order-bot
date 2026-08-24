<?php
// درجِ سریعِ محلیِ موجودی برای جواهریان (روی خودِ سرورِ cPanel اجرا می‌شود).
// مستقر در: /home/user/jewel_apply_stock.php  (مالک: shop)
// اجرا:  su -s /bin/bash shop -c "/opt/cpanel/ea-php85/root/usr/bin/php /home/user/jewel_apply_stock.php"
// ورودی از stdin: خطوطِ "id<TAB>qty". manage_stock=true + stock_quantity را با ووکامرسِ محلی ست می‌کند
// (وضعیت خودکار از تعداد ساخته می‌شود: ۰=ناموجود). خروجی: "OK<TAB>id" یا "ERR<TAB>id<TAB>msg".
// چرا محلی: بدونِ شبکه، بدونِ حذفِ بدنهٔ هاست، بدونِ سربارِ per-request → به‌جای ۱۹۰۰ نوشتنِ HTTPِ کُند.
define("WP_USE_THEMES", false);
require "/home/user/public_html/wp-load.php";
if (!function_exists("wc_get_product")) { fwrite(STDERR, "no-woocommerce\n"); exit(2); }
wp_defer_term_counting(true);
wp_defer_comment_counting(true);
$ok = 0; $err = 0;
while (($line = fgets(STDIN)) !== false) {
  $line = trim($line);
  if ($line === "") continue;
  $parts = explode("\t", $line);
  if (count($parts) < 2) continue;
  $id = (int)$parts[0];
  $qty = (int)$parts[1];
  try {
    $p = wc_get_product($id);
    if (!$p) { echo "ERR\t$id\tnotfound\n"; $err++; continue; }
    $p->set_manage_stock(true);
    $p->set_stock_quantity($qty);
    $p->save();
    echo "OK\t$id\n"; $ok++;
  } catch (Throwable $e) {
    echo "ERR\t$id\t" . str_replace("\n", " ", $e->getMessage()) . "\n"; $err++;
  }
}
wp_defer_term_counting(false);
wp_defer_comment_counting(false);
fwrite(STDERR, "done ok=$ok err=$err\n");
