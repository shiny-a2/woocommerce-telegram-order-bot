<?php
// برگرداندنِ موجودیِ عددیِ محصولات (WC-aware). روی سرورِ cPanel، کاربر user.
// ورودی stdin: خطوطِ "id<TAB>qty".  manage_stock=true + stock_quantity=qty ؛ وضعیت از qty (>0=موجود).
// اجرا: su -s /bin/bash user -c "/opt/cpanel/ea-php85/root/usr/bin/php /home/user/restore_stock.php"
define("WP_USE_THEMES", false);
require "/home/user/public_html/wp-load.php";
if (!function_exists("wc_get_product")) { fwrite(STDERR, "no-woocommerce\n"); exit(2); }
wp_defer_term_counting(true);
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
    $p->set_stock_status($qty > 0 ? "instock" : "outofstock");
    $p->save();
    echo "OK\t$id\t" . $p->get_stock_status() . "\t" . $p->get_stock_quantity() . "\n"; $ok++;
  } catch (Throwable $e) {
    echo "ERR\t$id\t" . str_replace("\n", " ", $e->getMessage()) . "\n"; $err++;
  }
}
wp_defer_term_counting(false);
fwrite(STDERR, "done ok=$ok err=$err\n");
