<?php
// بازگردانیِ دقیقِ وضعیتِ موجودیِ یک برند از فایلِ پشتیبان (WC-aware). کاربر siteuser.
//
// ورودی stdin — عیناً همان چهار ستونِ فایلِ پشتیبان:
//     id <TAB> stock_status <TAB> stock <TAB> manage_stock
//
// چرا جدا از restore_stock.php: آن اسکریپت همیشه manage_stock=true می‌گذارد و برای محصولاتِ
// «شرکتیِ» بدونِ تعداد غلط است — گذاشتنِ manage_stock روی محصولی که تعداد ندارد باعث می‌شود
// ووکامرس وضعیت را از تعدادِ خالی حساب کند و بی‌صدا به ناموجود برگردد.
// اینجا هر ردیف با همان حالتی که در پشتیبان ثبت شده برمی‌گردد:
//     manage_stock=yes → تعداد برمی‌گردد و وضعیت از تعداد می‌آید
//     manage_stock=no  → تعداد خاموش و وضعیت عیناً همان چیزی که بود
//
// اجرا: su -s /bin/bash siteuser -c "/opt/cpanel/ea-php85/root/usr/bin/php /home/siteuser/restore_brand_stock.php"
// خروجی: OK <TAB> id <TAB> status <TAB> qty   ·   SKIP <TAB> id <TAB> unchanged   ·   ERR <TAB> id <TAB> reason
define("WP_USE_THEMES", false);
require "/home/siteuser/public_html/wp-load.php";
if (!function_exists("wc_get_product")) { fwrite(STDERR, "no-woocommerce\n"); exit(2); }
wp_defer_term_counting(true);

$ok = 0; $skip = 0; $err = 0;
while (($line = fgets(STDIN)) !== false) {
  $line = trim($line);
  if ($line === "") continue;
  $parts = explode("\t", $line);
  if (count($parts) < 4) { $err++; continue; }
  $id     = (int)$parts[0];
  $status = trim($parts[1]) === "instock" ? "instock" : "outofstock";
  $qtyRaw = trim($parts[2]);
  $manage = strtolower(trim($parts[3])) === "yes";

  try {
    $p = wc_get_product($id);
    if (!$p) { echo "ERR\t$id\tnotfound\n"; $err++; continue; }

    // اگر همین حالا درست است، دست نزن — نوشتنِ بی‌دلیل هم کند است هم لاگ را شلوغ می‌کند.
    $curStatus = $p->get_stock_status();
    $curManage = $p->get_manage_stock();
    $curQty    = $p->get_stock_quantity();
    if ($curStatus === $status && $curManage === $manage &&
        (!$manage || (string)$curQty === $qtyRaw)) {
      echo "SKIP\t$id\tunchanged\n"; $skip++; continue;
    }

    if ($manage) {
      $qty = (int)$qtyRaw;
      $p->set_manage_stock(true);
      $p->set_stock_quantity($qty);
      $p->set_stock_status($qty > 0 ? "instock" : "outofstock");
    } else {
      $p->set_manage_stock(false);
      $p->set_stock_quantity(null);
      $p->set_stock_status($status);
    }
    $p->save();
    echo "OK\t$id\t" . $p->get_stock_status() . "\t" . $p->get_stock_quantity() . "\n"; $ok++;
  } catch (Throwable $e) {
    echo "ERR\t$id\t" . str_replace("\n", " ", $e->getMessage()) . "\n"; $err++;
  }
}
wp_defer_term_counting(false);
fwrite(STDERR, "done ok=$ok skip=$skip err=$err\n");
