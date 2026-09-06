<?php
// ناموجود‌کردنِ همهٔ محصولاتِ منتشرشدهٔ چند برند (WC-aware، روی سرورِ cPanel).
// مستقر در: /home/user/set_brand_oos.php  (مالک: user)
// اجرا:  su -s /bin/bash user -c "/opt/cpanel/ea-php85/root/usr/bin/php /home/user/set_brand_oos.php"
// برندها با term_id (هم pa_نام-برند هم product_cat) در $TERMS پایین تعریف می‌شوند.
// فقط محصولاتی که «ناموجود» نیستند تغییر می‌کنند (ایدمپوتنت). خروجی: OK<TAB>id یا ERR<TAB>id<TAB>msg.
// برای برگرداندن، از بکاپِ TSV و اسکریپتِ restore استفاده کنید.
define("WP_USE_THEMES", false);
require "/home/user/public_html/wp-load.php";
if (!function_exists("wc_get_product")) { fwrite(STDERR, "no-woocommerce\n"); exit(2); }
global $wpdb;

// کاترپیلار(کت): pa_نام-برند 5422 + product_cat 21572 ؛ فیلیپ‌پلین: pa_نام-برند 21173 + product_cat 22706
$TERMS = [5422, 21173, 21572, 22706];
$in = implode(",", array_map("intval", $TERMS));
$ids = $wpdb->get_col(
  "SELECT DISTINCT tr.object_id
     FROM {$wpdb->term_relationships} tr
     JOIN {$wpdb->term_taxonomy} tt ON tt.term_taxonomy_id = tr.term_taxonomy_id
     JOIN {$wpdb->posts} p ON p.ID = tr.object_id
    WHERE tt.term_id IN ($in)
      AND p.post_type = 'product'
      AND p.post_status = 'publish'"
);
fwrite(STDERR, "found=" . count($ids) . "\n");

wp_defer_term_counting(true);
wp_defer_comment_counting(true);
$ok = 0; $skip = 0; $err = 0;
foreach ($ids as $id) {
  $id = (int)$id;
  try {
    $p = wc_get_product($id);
    if (!$p) { echo "ERR\t$id\tnotfound\n"; $err++; continue; }
    if ($p->get_stock_status() === "outofstock") { $skip++; continue; }  // از قبل ناموجود
    if ($p->get_manage_stock()) { $p->set_stock_quantity(0); }
    $p->set_stock_status("outofstock");
    $p->set_backorders("no");
    $p->save();
    echo "OK\t$id\n"; $ok++;
  } catch (Throwable $e) {
    echo "ERR\t$id\t" . str_replace("\n", " ", $e->getMessage()) . "\n"; $err++;
  }
}
wp_defer_term_counting(false);
wp_defer_comment_counting(false);
fwrite(STDERR, "done ok=$ok skip=$skip err=$err\n");
