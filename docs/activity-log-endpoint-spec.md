# اسپکِ اندپوینتِ «لاگِ فعالیتِ per-user» برای افزونه‌ی a2-crm-plugin — نسخه‌ی جامع

هدف: رباتِ مدیریت بتواند ادعای هر کارمند را با «کارِ واقعیِ ثبت‌شده در سایت» بسنجد.
مثال‌ها:
- کارمند می‌گوید «۱۰۰ محصول دسته‌بندی کردم» → ربات می‌پرسد «کاربرِ X امروز چند بار `product_categorized` زد؟».
- کارمند می‌گوید «۳۰ سفارش را پیگیری/تغییرِ وضعیت دادم» → ربات `order_status_changed`ِ او را می‌شمارد.
- کارمند می‌گوید «۸ ساعت آنلاین بودم» → ربات زوجِ `login`/`logout` را می‌سنجد.

این کار در همان افزونه‌ی وردپرسیِ CRM (`a2crm/v1/tg`) و با همان توکن (`X-A2-Token`) انجام می‌شود.

---

## ۰) دامنه — این اسپک چه‌قدر می‌گیرد؟ (خواندنِ اجباری)
نسخه‌ی قبلیِ این فایل فقط **محصول + دو کنشِ لید** را می‌گرفت. این نسخه دامنه را کامل می‌کند و آن را **قابلِ‌تنظیم** می‌کند تا بتوانی از «فقط CRM» تا «همه‌ی اکتیویتیِ سایت» را با یک ثابت انتخاب کنی.

نکته‌ی معماری: افزونه از قبل دو منبعِ لاگِ داخلی دارد که نباید دوباره‌کاری کرد:
- `a2_audit_log` (actor_id, actor_name, entity_type, entity_id, action, changes(JSON), ip, created_at) — روی upsertهای contact/lead/deal/order/task/quote/inperson به‌طورِ خودکار پر می‌شود.
- `a2_lead_status_log` (phone, status, user_id, user_name, follow_up_at, created_at) — روی تغییرِ وضعیتِ لید.

بنابراین سیاست:
- **حوزه‌ی `crm`** را از همین جدول‌های موجود تغذیه کن (نرمالایز کن، جدولِ جدید لازم نیست) — یا اگر ساده‌تر است، در همان هندلرهای REST یک ردیف در جدولِ یکپارچه‌ی زیر هم بنویس.
- **حوزه‌های `shop` و `all`** در audit_log نیستند؛ برای این‌ها هوک‌های وردپرسیِ این اسپک را روی جدولِ یکپارچه‌ی `a2crm_activity` بنویس.
- برای یکدستیِ خروجیِ ربات، اندپوینتِ `/activity` هر دو منبع (audit_log و a2crm_activity) را **در یک شِمای واحد** برمی‌گرداند (بخشِ ۵).

---

## ۱) سطحِ پوششِ قابلِ‌تنظیم — `A2CRM_ACTIVITY_SCOPE`
در `wp-config.php` یا بالای فایلِ افزونه یک ثابت تعریف کن. اگر تعریف نشده باشد، پیش‌فرض **`'all'`** است — یعنی هر کارِ معنی‌دارِ کارمند (محصول، سفارش، محتوا/برگه، سئو، مدیا، نظر، کاربر، حضور) ثبت می‌شود.

```php
// مقادیرِ مجاز: 'crm' | 'shop' | 'all'  — پیش‌فرض روی all (همه‌چیز)
if ( ! defined('A2CRM_ACTIVITY_SCOPE') ) define('A2CRM_ACTIVITY_SCOPE', 'all');

// ردیابیِ حضور (login/logout): سیگنالِ ضعیف و غیرقطعی — «آنلاین‌بودن ≠ کارکردن».
// مستقل از scope؛ حتی در سطحِ all می‌توانی جدا خاموشش کنی بدونِ از دست دادنِ بقیه‌ی لاگ‌ها.
if ( ! defined('A2CRM_TRACK_PRESENCE') ) define('A2CRM_TRACK_PRESENCE', true);
```

هر سطح چه هوک‌هایی را فعال می‌کند:

| سطح | چه چیزی لاگ می‌شود | حوزه‌های فعال |
|---|---|---|
| `crm` | فقط کنش‌های CRM روی لید/تماس: تغییرِ وضعیت، نوت، اساین، آپدیتِ فیلد | `crm` |
| `shop` | همه‌ی `crm` + مدیریتِ محصول (ساخت/ویرایش/انتشار/حذف/قیمت/موجودی/دسته/تگ/عکس) + سفارش‌ها (وضعیت/نوت/refund/ویرایش) + کوپن‌ها + ترم‌ها | `crm`, `product`, `order`, `coupon`, `term` |
| **`all`** ⭐ (پیش‌فرض) | همه‌ی `shop` + مدیا (آپلود/حذف) + محتوا/برگه (post/page) + **سئو (عنوان/توضیح/کلیدواژه)** + نظرات/ری‌ویو + کاربران (ساخت/ویرایش) + ورود/خروجِ کارمند (اختیاری با `A2CRM_TRACK_PRESENCE`) | همه |

تابعِ کمکیِ گِیت (هر ثبت از این عبور کند):

```php
function a2crm_scope_allows( $scope_needed ) {
  $level = defined('A2CRM_ACTIVITY_SCOPE') ? A2CRM_ACTIVITY_SCOPE : 'shop';
  $rank  = array('crm' => 1, 'shop' => 2, 'all' => 3);
  $have  = isset($rank[$level]) ? $rank[$level] : 2;
  $need  = isset($rank[$scope_needed]) ? $rank[$scope_needed] : 2;
  return $have >= $need;
}
```
هر حوزه در ستونِ «سطح» جدولِ بخشِ ۳ حداقل‌سطحِ لازمش را دارد؛ ثبت فقط وقتی `a2crm_scope_allows(<آن سطح>)` درست باشد انجام می‌شود.

> نگاشتِ حوزه‌ها به `scope_needed`: کنش‌های CRM → `crm`؛ همه‌ی حوزه‌های shop (`product`/`order`/`coupon`/`term`) → `shop`؛ حوزه‌های `media`/`content`/`comment`/`user`/`presence` → `all`.

---

## ۲) پیش‌نیازِ حیاتی — لینکِ پرسنل به کاربرِ وردپرس
ربات کارمند را با «آی‌دیِ تلگرام / یوزرنیم» می‌شناسد؛ سایت با «آی‌دیِ کاربرِ وردپرس». پس نگاشت لازم است:
- اندپوینتِ موجودِ `/agents` همین حالا `{user_id, display_name}` می‌دهد که `user_id` همان WP user ID است.
- هر کارمند باید یک کاربرِ وردپرسِ **جدا** داشته باشد و با همان لاگین کند تا کارش زیرِ نامِ خودش ثبت شود، نه زیرِ ادمینِ مشترک.
- اگر کارمند‌ها با یک کاربرِ مشترک لاگین کنند، لاگ بی‌معنی می‌شود؛ اول این را اصلاح کنید.
- استثنا: کنش‌های CRM از تلگرام با **توکنِ مشترک** می‌آیند و `get_current_user_id()` صفر است؛ برای این‌ها actor را از `actor_name`/نگاشتِ `/agents` بگیر و **صریح** به تابعِ لاگ بده (بخشِ ۴-۲).

---

## ۳) جدولِ کاملِ هوک‌ها — چه چیزی، کِی، در کدام سطح

محافظت‌های الزامیِ مشترک در ابتدای هر هندلرِ وردپرسی:
```php
if ( defined('DOING_AUTOSAVE') && DOING_AUTOSAVE ) return;
if ( isset($post_id) && wp_is_post_revision($post_id) ) return;
$uid = get_current_user_id();
if ( ! $uid ) return; // تغییرِ برنامه‌ای/سیستمی/سینک را ثبت نکن — فقط کارِ انسانِ لاگین‌کرده
// dedup در همان request:  static $seen; if (!empty($seen[$key])) return; $seen[$key]=1;
```
> فلسفه‌ی guardِ `uid=0`: تقریباً همه‌ی گذارهای سیستمی (کاهشِ موجودی هنگامِ فروش، تغییرِ وضعیتِ سفارش توسطِ درگاه، refundِ خودکار، نوتِ سیستمیِ Woo، ثبت‌نامِ خودجوشِ مشتری) actorِ صفر دارند و همین guard حذف‌شان می‌کند؛ فقط کارِ دستیِ کارمند می‌ماند.

نام‌های `action` را **دقیقاً** همین‌ها بگذار تا ربات بشناسد.

### حوزه‌ی CRM (سطح: `crm`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| CRM | درونِ هندلرِ REST `POST /lead-status` پس از نوشتِ موفق | `lead_status` | `lead` | `{"status","label","follow_up_at","phone"(نرمال/هش)}` | actor صریح از `actor_name`/`/agents`؛ اگر status عوض نشد رد کن | `crm` |
| CRM | درونِ هندلرِ REST `POST /note` | `lead_note` | `lead` | `{"excerpt"(≤~120char),"phone"}` | actor صریح؛ فقط excerpt، نه متنِ کامل/حساس؛ پس از نوشتِ موفق | `crm` |
| CRM | درونِ هندلرِ REST `POST /assign` | `lead_assigned` | `lead` | `{"assigned_to","assigned_name","phone"}` | actor صریح (کسی که اساین زد)؛ اگر assigned_to عوض نشد رد کن | `crm` |
| CRM | درونِ هندلرِ REST `POST /update` | `lead_updated` | `lead` | `{"entity":lead|contact,"fields":[کلیدهای عوض‌شده]}` | actor صریح؛ فقط نامِ فیلدها، نه مقادیرِ حساس | `crm` |

### حوزه‌ی محصول (سطح: `shop`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| محصول | `woocommerce_new_product` / `..._variation` | `product_created` | `product` | `{"name","sku","type":simple|variable,"status"}` (واریِیشن: `parent_id`) | status=='auto-draft' رد؛ uid=0 رد؛ `static $seen[$id]` | `shop` |
| محصول | `woocommerce_update_product` / `..._variation` | `product_updated` | `product` | `{"name"}` | یک ردیفِ چتر در هر request؛ ریزتغییرها را اکشن‌های اختصاصی بگیرند؛ AUTOSAVE/revision/uid=0 رد | `shop` |
| محصول | `transition_post_status` (`post_type==='product'`) | `product_status_changed` | `product` | `{"old","new"}` (انتشار: `new==='publish' && old!=='publish'`) | فقط `old!==new`؛ auto-draft/inherit رد؛ uid=0 رد | `shop` |
| محصول | `wp_trash_post` (trash) و `before_delete_post` (delete)، product | `product_deleted` | `product` | `{"name","sku","mode":trash|delete}` | post_type چک؛ trash/delete دوبار نشمار؛ uid=0 رد | `shop` |
| محصول | `updated_post_meta` روی `{_regular_price,_sale_price,_price}` (+pre) | `price_changed` | `product` | `{"key","old","new"}` | فقط این ۳ key؛ `(string)old===(string)new` رد؛ dedup per (id,key)؛ uid=0 رد | `shop` |
| محصول | `woocommerce_product_set_stock`/`..._variation_set_stock` + `woocommerce_product_set_stock_status` | `stock_changed` | `product` | `{"old_qty","new_qty"}` یا `{"old_status","new_status"}` | پیش‌مقدار قبل از set؛ کاهشِ فروش uid=0→رد؛ dedup per product؛ uid=0 رد | `shop` |
| محصول | `set_object_terms` (`taxonomy==='product_cat'`) | `product_categorized` | `product` | `{"added":[ids],"removed":[ids]}` | فقط diff($tt_ids,$old_tt_ids) غیرخالی؛ uid=0 رد | `shop` |
| محصول | `set_object_terms` (`taxonomy==='product_tag'`) | `product_tagged` | `product` | `{"added":[ids],"removed":[ids]}` | فقط `product_tag`؛ فقط diff غیرخالی؛ uid=0 رد | `shop` |
| محصول | `updated_post_meta` روی `{_thumbnail_id,_product_image_gallery}` | `image_changed` | `product` | `{"key","old","new"}` (گالری: تعدادِ عکس) | فقط این ۲ key و post_type=product؛ old===new رد؛ dedup per (id,key)؛ uid=0 رد | `shop` |

### حوزه‌ی سفارش (سطح: `shop`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| سفارش | `woocommerce_order_status_changed` | `order_status_changed` | `order` | `{"old","new"}` | uid=0 رد (گذارِ درگاه/cron حذف)؛ `from===to` رد | `shop` |
| سفارش | `woocommerce_order_note_added` | `order_note_added` | `order` | `{"note_id","is_customer_note":0|1,"excerpt"(≤~120char)}` | نوتِ سیستمی uid=0→رد؛ بدونِ داده‌ی حساس | `shop` |
| سفارش | `woocommerce_order_refunded` / `woocommerce_create_refund` | `order_refunded` | `order` | `{"refund_id","amount","reason"}` | uid=0 رد؛ amount از شیِ refund نه متا | `shop` |
| سفارش | `woocommerce_process_shop_order_meta` / `woocommerce_update_order` | `order_edited` | `order` | `{"fields":[کلیدهای عوض‌شده]}` | uid=0 رد؛ `$seen[$order_id]`؛ اگر فقط وضعیت عوض شد، status کافی است | `shop` |

### حوزه‌ی کوپن (سطح: `shop`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| کوپن | `woocommerce_new_coupon` (fallback: `save_post_shop_coupon` + `$update===false`) | `coupon_created` | `coupon` | `{"code","discount_type","amount"}` | auto-draft رد؛ dedup per id؛ uid=0 رد | `shop` |
| کوپن | `woocommerce_update_coupon` (fallback: `save_post_shop_coupon` + `$update===true`) | `coupon_updated` | `coupon` | `{"code","amount"}` | AUTOSAVE/revision رد؛ `$seen[$id]`؛ uid=0 رد | `shop` |
| کوپن | `wp_trash_post`/`before_delete_post` (`shop_coupon`) | `coupon_deleted` | `coupon` | `{"code","mode":trash|delete}` | post_type چک؛ trash/delete دوبار نشمار؛ uid=0 رد | `shop` |

### حوزه‌ی ترم/دسته (سطح: `shop`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| ترم | `created_term` (`taxonomy in {product_cat,product_tag}`) | `term_created` | `term` | `{"taxonomy","name","parent"}` | فقط تاکسونومیِ محصول؛ uid=0 رد | `shop` |
| ترم | `edited_term` (همان تاکسونومی‌ها) | `term_updated` | `term` | `{"taxonomy","name"}` | dedup per term؛ uid=0 رد | `shop` |
| ترم | `delete_term` (همان تاکسونومی‌ها) | `term_deleted` | `term` | `{"taxonomy","name"}` (از `$deleted_term`) | نام از آرگومانِ `$deleted_term`؛ uid=0 رد | `shop` |

### حوزه‌ی مدیا (سطح: `all`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| مدیا | `add_attachment` | `media_uploaded` | `attachment` | `{"filename"(basename),"mime","parent"}` | uid=0 رد؛ در صورتِ نیاز فقط `image/*`؛ فقط basename | `all` |
| مدیا | `delete_attachment` | `media_deleted` | `attachment` | `{"filename","mime"}` | mime را قبل از حذف بگیر؛ uid=0 رد | `all` |

### حوزه‌ی محتوا (سطح: `all`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| محتوا | `transition_post_status` (`post_type in {post,page}` و `new==='publish' && old!=='publish'`) | `content_published` | `post` | `{"post_type","title"}` | auto-draft/inherit/revision رد؛ فقط post/page؛ uid=0 رد | `all` |
| محتوا | `post_updated` (`post_type in {post,page}` و `post_before->post_status==='publish'`) | `content_updated` | `post` | `{"post_type","title"}` | فقط اگر قبلاً منتشر بوده؛ AUTOSAVE/revision رد؛ `$seen[$id]`؛ uid=0 رد | `all` |
| محتوا | `wp_trash_post`/`before_delete_post` (`post_type in {post,page}`) | `content_deleted` | `post` | `{"post_type","title","mode":trash|delete}` | post_type چک؛ trash/delete دوبار نشمار؛ uid=0 رد | `all` |

### حوزه‌ی سئو (سطح: `all`) — روی محصول و نوشته/برگه
کارِ سئو (تنظیمِ عنوانِ سئو، متادیسکریپشن، کلیدواژه‌ی کانونی) در متای پست ذخیره می‌شود. افزونه‌ی سئو را در زمانِ اجرا تشخیص بده و **هر کلیدی که موجود بود** را رصد کن:
- Yoast: `_yoast_wpseo_title`, `_yoast_wpseo_metadesc`, `_yoast_wpseo_focuskw`
- Rank Math: `rank_math_title`, `rank_math_description`, `rank_math_focus_keyword`
- SEOPress: `_seopress_titles_title`, `_seopress_titles_desc`

| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| سئو | `updated_post_meta`/`added_post_meta` روی کلیدهای سئوِ بالا | `seo_updated` | `product` یا `post` | `{"key","plugin":yoast|rankmath|seopress,"post_type"}` | فقط کلیدهای سئو؛ `(string)old===(string)new` رد؛ **dedup per (post_id) در همان request** (چند فیلدِ سئو با هم = یک ردیف)؛ AUTOSAVE/revision/uid=0 رد | `all` |

> `object_type` را از `get_post_type($post_id)` بگیر (محصول یا نوشته/برگه). اگر خواستی سئوِ **محصول** حتی در سطحِ `shop` هم ثبت شود، برای وقتی که `post_type==='product'` است `scope_needed` را `shop` بگذار و در بقیه `all`.

### حوزه‌ی نظر/ری‌ویو (سطح: `all`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| نظر | `transition_comment_status` (`comment_type==='review'` یا پستِ هدف product) | `review_status_changed` | `comment` | `{"old","new":approved|hold|spam|trash,"product_id","rating"}` | فقط ری‌ویو/کامنتِ محصول؛ uid=0 رد؛ new/old نرمالایز | `all` |
| نظر | `wp_set_comment_status` (fallback — فقط یکی از این دو را فعال کن) | `review_status_changed` | `comment` | `{"status","product_id"}` | با `get_comment` نوع/هدف را فیلتر کن؛ uid=0 رد | `all` |
| نظر | `delete_comment`/`trash_comment` | `review_deleted` | `comment` | `{"product_id","mode":trash|delete}` | نوع را قبل از حذف بگیر؛ trash/delete دوبار نشمار؛ uid=0 رد | `all` |

### حوزه‌ی کاربر (سطح: `all`)
| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| کاربر | `user_register` | `user_created` | `user` | `{"login","role","created_by":<uid کارمند>}` | uid=0 رد (ثبت‌نامِ خودجوشِ مشتری حذف)؛ در صورتِ نیاز `customer_self_register` با actor=0 جدا | `all` |
| کاربر | `profile_update` | `user_updated` | `user` | `{"login","changed":[کلیدها از $old_user_data]}` | uid=0 رد؛ dedup per user؛ رمز/متای حساس در detail نیاور | `all` |

### حوزه‌ی حضور (سطح: `all` + کلیدِ `A2CRM_TRACK_PRESENCE`)
> ⚠️ **سیگنالِ ضعیف و غیرقطعی:** `login`/`logout` فقط «آنلاین‌بودن» را نشان می‌دهد، نه «کارکردن» — کارمند می‌تواند لاگین بماند و کاری نکند، یا با یک لاگین کلِ روز کار کند. **سنجه‌ی واقعیِ کار، همان `counts`ِ کنش‌هاست؛ حضور فقط مکمل است.** با `define('A2CRM_TRACK_PRESENCE', false)` می‌توان بدونِ تغییرِ scope خاموشش کرد.

| حوزه | هوک | `action` | `object_type` | `detail` | guard | حداقل‌سطح |
|---|---|---|---|---|---|---|
| حضور | `wp_login` | `login` | `user` | `{"login","ip"(اختیاری/هش),"ua"(کوتاه)}` | فقط اگر `A2CRM_TRACK_PRESENCE`؛ uid از `$user->ID`؛ در صورتِ نیاز فقط نقش‌های کارمند؛ IP طبقِ حریمِ خصوصی | `all` |
| حضور | `wp_logout` | `logout` | `user` | `{"login"}` | فقط اگر `A2CRM_TRACK_PRESENCE`؛ uid ابتدا از آرگومان وگرنه `get_current_user_id()` قبل از پاک‌شدنِ نشست؛ سشنِ منقضی logout نمی‌زند | `all` |

---

## ۴) جدولِ لاگ و توابعِ کمکی

### ۴-۱) جدولِ یکپارچه
```sql
CREATE TABLE {$wpdb->prefix}a2crm_activity (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id      BIGINT UNSIGNED NOT NULL DEFAULT 0,   -- WP user که تغییر را زد
  user_login   VARCHAR(60)     NOT NULL DEFAULT '',
  action       VARCHAR(40)     NOT NULL,             -- زیرِ «هوک‌ها» ببین
  object_type  VARCHAR(20)     NOT NULL DEFAULT '',  -- product|lead|order|coupon|term|attachment|post|comment|user
  object_id    BIGINT UNSIGNED NOT NULL DEFAULT 0,
  detail       TEXT            NULL,                 -- JSON
  created_gmt  DATETIME        NOT NULL,             -- زمانِ GMT (منبع)
  created_ts   BIGINT UNSIGNED NOT NULL,             -- epoch برای فیلترِ سریع
  PRIMARY KEY (id),
  KEY idx_user_ts (user_id, created_ts),
  KEY idx_action_ts (action, created_ts),
  KEY idx_obj (object_type, object_id)
) {$charset_collate};
```
با `dbDelta()` در هوکِ فعال‌سازیِ افزونه بساز.

### ۴-۲) تابعِ ثبت (دو امضا — uidِ خودکار و actorِ صریح برای CRM)
```php
function a2crm_log( $action, $object_type, $object_id, $detail = null, $scope_needed = 'shop', $actor_id = null ) {
  if ( ! a2crm_scope_allows( $scope_needed ) ) return;      // گِیتِ سطحِ پوشش
  global $wpdb;
  $uid = ( $actor_id !== null ) ? (int) $actor_id : get_current_user_id();  // CRM: actor صریح
  if ( ! $uid ) return;                                     // فقط کارِ انسانِ شناخته‌شده
  $u = get_userdata( $uid );
  $wpdb->insert( $wpdb->prefix.'a2crm_activity', array(
    'user_id'     => $uid,
    'user_login'  => $u ? $u->user_login : '',
    'action'      => $action,
    'object_type' => $object_type,
    'object_id'   => (int) $object_id,
    'detail'      => $detail ? wp_json_encode( $detail ) : null,
    'created_gmt' => current_time( 'mysql', true ),
    'created_ts'  => time(),
  ) );
}
```
- برای هوک‌های وردپرسی: `a2crm_log('product_updated','product',$id,$detail,'shop')`.
- برای CRM از توکنِ مشترک: `a2crm_log('lead_status','lead',$lead_id,$detail,'crm',$actor_wp_id)` — که `$actor_wp_id` از نگاشتِ `actor_name`→`/agents` می‌آید.

### ۴-۳) نگاشتِ منبعِ موجود (`a2_audit_log`) به شِمای واحد (فقط خواندنی، در اندپوینت)
اگر ترجیح می‌دهی برای حوزه‌ی CRM از جدولِ موجود استفاده کنی، اندپوینت هنگامِ پاسخ ردیف‌های `a2_audit_log` و `a2_lead_status_log` را به همین کلیدها نگاشت کند: `actor_id→user_id`, `actor_name→user_login`, `entity_type→object_type`, `entity_id→object_id`, `action→action` (پیشوندِ `crm_` بزن تا با اکشن‌های shop اشتباه نشود), `changes→detail`, `created_at→created_gmt/created_ts`. توصیه: برای سادگیِ ربات، همان `lead_status`/`lead_note`/`lead_assigned`/`lead_updated` را در `a2crm_activity` هم بنویس تا یک منبع باشد.

---

## ۵) اندپوینتِ REST
زیرِ همان namespaceِ CRM، با همان احرازِ توکن.

- مسیر: `GET /wp-json/a2crm/v1/tg/activity`
- هدرِ الزامی: `X-A2-Token: <همان توکنِ CRM>` (از `permission_callback`ِ موجود استفاده کن)
- پارامترها:
  - `user_id` (یا `user_login`) — کارمندِ موردِنظر (الزامی برای per-user)
  - `from`, `to` — بازه به‌صورتِ ISO میلادی `YYYY-MM-DD` (ربات خودش از شمسی تبدیل می‌کند)
  - `action` — اختیاری، فیلترِ یک اکشن
  - `object_type` — اختیاری، فیلترِ یک نوع
  - `group` — اگر `1` باشد، شمارشِ تجمیعیِ per-action؛ وگرنه ردیف‌های خام

پاسخِ حالتِ تجمیعی (`group=1`) — دقیقاً همین شکل تا ربات پارس کند:
```json
{
  "ok": true,
  "user_id": 42,
  "user_login": "reza",
  "from": "2026-07-05",
  "to": "2026-07-05",
  "scope": "shop",
  "counts": {
    "product_created": 8,
    "product_updated": 150,
    "product_categorized": 100,
    "price_changed": 40,
    "stock_changed": 22,
    "image_changed": 12,
    "order_status_changed": 30,
    "order_note_added": 18,
    "order_refunded": 2,
    "coupon_created": 3,
    "lead_status": 55,
    "lead_note": 41,
    "lead_assigned": 9,
    "lead_updated": 14
  },
  "by_object": {
    "product": 332,
    "order": 50,
    "coupon": 3,
    "lead": 119
  },
  "total": 504
}
```
- `scope` = مقدارِ فعلیِ `A2CRM_ACTIVITY_SCOPE` تا ربات بداند چه چیزی اصلاً قابلِ‌ثبت بوده.
- پاسخِ حالتِ خام (بدونِ `group`): `{"ok":true,"rows":[{action,object_type,object_id,detail,created_gmt}, ...],"count":N}` با `LIMIT` امن (مثلاً ۵۰۰).
- بازه را با `created_ts` فیلتر کن؛ `from` را ابتدای روز و `to` را انتهای روز به وقتِ فروشگاه (Asia/Tehran) به epoch تبدیل کن.

نمونه‌ی کوئری (تجمیعی):
```php
$rows = $wpdb->get_results( $wpdb->prepare(
  "SELECT action, object_type, COUNT(*) c
     FROM {$wpdb->prefix}a2crm_activity
    WHERE user_id=%d AND created_ts>=%d AND created_ts<%d
    GROUP BY action, object_type", $user_id, $from_ts, $to_ts ), ARRAY_A );
// سپس counts (per-action) و by_object (per-object_type) و total را در PHP جمع بزن.
```
اگر حوزه‌ی CRM را از `a2_audit_log`/`a2_lead_status_log` می‌خوانی، یک `UNION ALL` با نگاشتِ ستون‌های بخشِ ۴-۳ اضافه کن یا دو کوئری را در PHP ادغام کن.

---

## ۶) ایندکس و پاکسازی (cron)
- ایندکس‌ها در بخشِ ۴-۱ هست.
- cronِ روزانه ردیف‌های قدیمی‌تر از ۱۸۰ روز را پاک کند:
```php
if ( ! wp_next_scheduled('a2crm_activity_prune') )
  wp_schedule_event( time()+3600, 'daily', 'a2crm_activity_prune' );
add_action('a2crm_activity_prune', function(){
  global $wpdb;
  $wpdb->query( $wpdb->prepare(
    "DELETE FROM {$wpdb->prefix}a2crm_activity WHERE created_ts < %d",
    time() - 180*86400 ) );
});
```
> اگر حجمِ `login`/`product_updated` زیاد شد، می‌توانی برای این دو retention کوتاه‌تری (مثلاً ۹۰ روز) بگذاری.

---

## ۷) امنیت
- فقط با توکن؛ بدونِ توکن هیچ خروجی.
- همه‌ی ورودی‌ها sanitize و همه‌ی کوئری‌ها `$wpdb->prepare`.
- اندپوینت فقط خواندنی است؛ چیزی در سایت تغییر نمی‌دهد.
- هیچ داده‌ی حساسی (رمز/توکن/موبایلِ کامل) در پاسخ یا در `detail` نیاید — تلفن را نرمال/هش، نوت را فقط excerpt.
- IPِ ورود را طبقِ سیاستِ حریمِ خصوصی نگه‌دار یا هش کن.

---

## ۸) جدولِ «چه چیزی گرفته می‌شود / چه چیزی نه»
با فرضِ `A2CRM_ACTIVITY_SCOPE='all'` همه‌ی سطرهای زیر گرفته می‌شوند؛ ستونِ «سطحِ لازم» می‌گوید در کدام مقدارِ ثابت فعال است.

| موضوع | گرفته می‌شود؟ | action | سطحِ لازم |
|---|---|---|---|
| ساخت/ویرایش/انتشار/حذفِ محصول | بله | product_created/updated/status_changed/deleted | shop |
| قیمت/موجودی/دسته/تگ/عکسِ محصول | بله | price_changed, stock_changed, product_categorized, product_tagged, image_changed | shop |
| تغییرِ وضعیتِ سفارش | بله | order_status_changed | shop |
| یادداشتِ سفارش / refund / ویرایشِ سفارش | بله | order_note_added, order_refunded, order_edited | shop |
| ساخت/ویرایش/حذفِ کوپن | بله | coupon_created/updated/deleted | shop |
| ساخت/ویرایش/حذفِ دسته و تگ (ترم) | بله | term_created/updated/deleted | shop |
| کنش‌های CRM روی لید (وضعیت/نوت/اساین/آپدیت) | بله | lead_status, lead_note, lead_assigned, lead_updated | crm |
| آپلود/حذفِ مدیا | بله | media_uploaded, media_deleted | all |
| انتشار/ویرایش/حذفِ نوشته/برگه | بله | content_published/updated/deleted | all |
| ویرایشِ سئو (عنوان/توضیح/کلیدواژه) روی محصول و برگه | بله | seo_updated | all |
| تأیید/رد/حذفِ نظر و ری‌ویو | بله | review_status_changed, review_deleted | all |
| ساخت/ویرایشِ کاربر | بله | user_created, user_updated | all |
| ورود/خروجِ کارمند (حضور — سیگنالِ ضعیف) | بله (اختیاری) | login, logout | all + `A2CRM_TRACK_PRESENCE` |
| تغییرِ خودکارِ سیستمی (فروش، درگاه، cron، سینک، ایمپورت) | خیر (عمداً) | — | — (uid=0 حذف می‌شود) |
| ثبت‌نامِ خودجوشِ مشتری در فرانت | خیر مگر جدا فعال کنی | (customer_self_register) | all + پیکربندی |
| تنظیماتِ افزونه/هسته، عملیاتِ شیپینگ، تراکنشِ درگاه | خیر (خارج از دامنه) | — | — |

خلاصه‌ی مقایسه با نسخه‌ی قبل: نسخه‌ی قبلی فقط `product_*` (بدونِ status/delete) + `lead_status` + `lead_note` را می‌گرفت. این نسخه سفارش/کوپن/ترم/مدیا/محتوا/نظر/کاربر/حضور و نیز `lead_assigned`/`lead_updated` را اضافه می‌کند و همه را پشتِ سطحِ قابلِ‌تنظیم می‌گذارد.

---

## ۹) سمتِ ربات (هماهنگیِ خروجی)
وقتی اندپوینت بالا آمد، در ربات این‌ها وصل می‌شود؛ خروجیِ بالا باید دقیقاً همین‌ها را بدهد:
- متدِ کلاینت: `crm.activity(user_id, date_from, date_to)` → `GET /activity?group=1`.
- نگاشتِ کارمندِ تلگرام → WP `user_id` از `/agents` (بخشِ پیش‌نیاز).
- در `_store_context`/context مغز، برای هر کارمند یک خطِ فشرده از `counts` تزریق می‌شود، مثلاً:
  «کارِ ثبت‌شده‌ی امروزِ رضا: محصول(ساخت=۸، ویرایش=۱۵۰، دسته=۱۰۰، قیمت=۴۰)، سئو=۶۰، سفارش(وضعیت=۳۰، نوت=۱۸)، محتوا/برگه=۵، لید(وضعیت=۵۵، نوت=۴۱، اساین=۹)، حضور(login=۲). scope=all».
- مغز `counts` را با ادعای کارمند می‌سنجد و مغایرت را در `flags` می‌آورد (مثلاً ادعای «۱۰۰ محصول ساختم» در برابرِ `product_created=8`).
- **حضور را نرم بخوان:** مغز نباید `login`/`logout` را سنجه‌ی اصلیِ کار بگیرد؛ فقط مکملِ counts است (چون غیرقطعی است — «آنلاین‌بودن ≠ کارکردن»).
- چون `scope` در پاسخ هست، ربات می‌داند اگر چیزی صفر است به‌خاطرِ «کارمند انجام نداده» بوده یا «آن حوزه در این سطح/کلید ثبت نمی‌شده» (مثلاً اگر مدیر عمداً scope را `shop` کند یا `A2CRM_TRACK_PRESENCE` را خاموش کند، `login` صفر است چون فعال نبوده، نه چون کارمند نیامده).

---

## ۱۰) خروجیِ نهایی که از AI می‌خواهیم
۱) جدولِ `a2crm_activity` ساخته شود.
۲) ثابتِ `A2CRM_ACTIVITY_SCOPE` (پیش‌فرض `all`) + کلیدِ مستقلِ `A2CRM_TRACK_PRESENCE` (پیش‌فرض `true`) + گِیتِ `a2crm_scope_allows()` اضافه شود.
۳) هوک‌های بخشِ ۳ طبقِ سطحِ لازمِ هرکدام رجیستر شوند (با همه‌ی guardها و dedupها).
۴) هوکِ `seo_updated` با تشخیصِ خودکارِ افزونه‌ی سئو (Yoast/Rank Math/SEOPress) روی محصول و برگه.
۵) حضور (`login`/`logout`) فقط پشتِ `A2CRM_TRACK_PRESENCE` و به‌عنوانِ سیگنالِ نرم.
۶) کنش‌های CRM با actorِ صریح لاگ شوند (توکنِ مشترک → نگاشتِ `/agents`).
۷) اندپوینتِ `/activity` با پاسخِ دقیقِ بخشِ ۵ (شاملِ `scope`, `counts`, `by_object`, `total`).
۸) prune cron.
۹) تأییدِ اینکه هر کارمند کاربرِ وردپرسیِ جدا دارد (وگرنه لاگ بی‌معنی است).
