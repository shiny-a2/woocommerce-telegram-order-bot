# Public release notes

## 2026-10-26 — Shared watch retailer brand matching

The catalog engine now accepts a retailer's expanded brand label only when
the requested brand is visible on the exact product page. This restored tested
image and specification discovery for two watch brands while leaving source
priorities under administrator control. The retailer's product sitemap is
cached across its image and data routes to avoid repeating a large catalog
scan. Two other administrator-selected stores already worked through the
generic connector and required no duplicate scraper.

Live reference checks and focused regression tests passed. Missing products
and existing products needing images entered the existing review workflow;
complete galleries were skipped. The generated review workbook and downstream
publication are still pending for the newly queued items.

## 2026-10-26 — Administrator-selected retailer coverage

The image and product-data engine now recognizes more retailer layouts already
selected in the brand-source settings. Tested routes include two watch stores
with WooCommerce catalog endpoints and four stores with different product URL
or image patterns. Model matching and image selection stay tied to the exact
reference; a shared retailer's brand hint is scoped to the intended brand.
Existing source priorities and the human Excel approval step are preserved.

Live product lookups and focused regression tests passed, and the restarted
service health check reported a running worker. References absent from the
storefront entered the existing review intake, while products with sufficient
images were skipped. The review workbook, Photoshop processing, final image
attachment and publication are still pending for those newly queued items.
Some configured retailers remain unverified, so this release does not claim
complete coverage of every brand-source setting.

## 2026-10-26 — Additional watch-source routes prepared

The source update added a supplemental Omax catalog route and a Louis Frrier retailer route. Both were checked against exact model references and real product images from the bot host. The Omax route uses a shared catalog cache for image and data lookups; the Louis Frrier route handles the retailer's compact model search and relative product links. Image selection rejects filenames for neighboring models on these routes, and duplicated hidden specification fields are excluded from the Louis Frrier data. Focused regression tests and production source lookups passed. After a database backup, the existing Excel intake queued the missing references without duplicate intake keys; a scheduled worker tick produced review workbooks while keeping product creation behind explicit approval. The image service remained healthy. The current Romanson retailer catalog did not cover the sampled older models, so no replacement route was registered. Product creation, image upload, and publication remain unverified for these new routes.

## 2026-10-26 — First priority watch-source batch

A reviewed official Omax source connector now supports product image and specification discovery. It accepts specifications only when the page's model matches the requested reference, reducing the risk of attaching another model's details. Two live models returned exact image matches and detailed specifications after deployment; the service health check passed. The current catalog covers only part of the historical Omax request list. Sampled older Romanson references and the Louis Frrier catalog did not yet yield a reliable live source, so no unverified fallback was added. Product creation, image upload, and publication were outside this source validation.

## 2026-10-25 — Pair-watch catalog sync

The daily supplier sync now recognizes a two-watch set by its two exact references. It updates the individual watches first, then derives the set's displayed price from their combined valid prices and its sellable quantity from the lower available quantity. An incomplete or ambiguous pair stays out of automatic pricing and appears in a separate review sheet. If either member update fails, the dependent set update is withheld. The production catalog was checked without customer transactions; the guarded reconciliation completed with no write or verification errors.

## 2026-10-19 — Supplier sync control

The administrator-only Telegram command for a supplier catalog feed now shows its actual status and offers explicit on/off controls in private chat. The switch coordinates the existing feed reader and scheduled catalog update, helping staff avoid a misleading menu item that previously did nothing. The feed remains off until an authorized administrator turns it on.

## 2026-10-19 — Topic-based content workspace

The internal manager now supports dedicated Telegram forum topics for caption and scenario drafts, a weekly plan preview, Instagram insights, competitor review and reports. This makes the content workflow easier to find and review in a busy group. A separate CRM topic can be reserved for the existing manager-only customer lookup. Topics are connected by an authorized manager inside the group; an invite link by itself does not establish access. The weekly Instagram summary can also reach its assigned reports topic. Existing private owner reports remain private, and drafts do not create tasks or publish posts.

If the optional Instagram analytics source is offline, the bot labels its last saved snapshot with its date and does not present old competitor figures as current performance.

The designated reports topic can also show today's staff performance and the monthly trend. Its daily staff summary is delivered there automatically after binding; private owner financial details remain outside the group.
