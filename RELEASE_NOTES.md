# Public release notes

## 2026-10-26 — Shared retailer brand matching

A watch retailer selected in the administrator's source settings now matches
one brand shown in Latin on product pages to its Persian catalog name. The
alias applies only to that brand, while its shared sitemap index serves both
image and product-data searches. Live samples for two brands returned exact
model pages, decoded images and visible specifications; focused connector and
transport tests passed.

The established review intake accepted missing models and routed existing
products needing images into the image queue after a database backup. Three
ambiguous storefront matches were held for identity review. The restarted
service reported a current worker; Excel approval, image processing and
publication are still pending for these items.

## 2026-10-26 — Complete shared multi-brand catalog scan

Another retailer already configured for several watch brands now uses one
complete product sitemap cache across those brand and data routes. Its public
search endpoint was unavailable to the bot, and the default catalog deadline
ended before all product maps loaded. The bounded route now finishes that
scan before searching exact model references. Live data checks for three
brands returned detailed product facts, and focused connector and transport
tests passed.

The established intake queued missing products and reused existing image
jobs, including references already waiting in earlier review batches. The
service was running after deployment. Excel approval, image processing and
publication remain separate pending steps.

## 2026-10-26 — Shared catalog for three configured brands

An Iranian retailer already selected in the administrator settings now uses
its complete product sitemap for three watch brands. The public product
search endpoint did not permit the bot's query, so the source uses a bounded
catalog scan shared across the brand and image/data routes. A model-specific
image filter excludes an unrelated gallery file. Live checks found exact
models, decoded product photos and visible specifications for sampled items;
focused connector and transport tests passed.

The existing review intake accepted products absent from the storefront and
created image work for incomplete existing products. One reference with an
ambiguous storefront lookup was held for manual identity review instead of
creating a duplicate. Reused jobs kept their earlier review-batch assignment.
The service remained healthy; review, Photoshop work and publication are
pending for the newly queued items.

## 2026-10-26 — Additional configured watch images

An administrator-selected retailer now participates in image discovery for
the configured watch brand. Its product pages use a different URL pattern and
its image filenames are opaque, so matching requires the full model on the
exact product page and the visible brand name. Accessory paths are excluded.
A live sample supplied a decoded high-resolution image; focused regression
tests and the restarted service health check passed.

The existing intake queued models absent from the storefront and routed
incomplete existing products to image work without duplicating a job already
in progress. Human review, Photoshop processing, attachment and publication
remain pending for the newly queued items.

## 2026-10-26 — Exact watch versus accessory matching

An administrator-selected official watch catalog now supplies tested images
and product specifications through its existing brand-source setting. Its
product pages and accessory pages can carry the same base model code, so the
source route excludes accessory paths and requires the complete model suffix.
It also reads labelled facts only from the exact product description. Live
checks covered a base model and a color variant; connector and transport
regression tests passed.

Missing references and existing products needing images entered the established
Excel review and image workflows after a database backup. Complete galleries
were skipped. The service restarted successfully; review, image processing
and publication for these queued products are still pending. Other configured
retailers remain under review and are not claimed as fully supported here.

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
