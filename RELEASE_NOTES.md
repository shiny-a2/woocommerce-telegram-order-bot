# Public release notes

## 2026-10-28 — More exact watch images and reviewable specifications

The catalog worker now has an image fallback for several Tornado models absent
from the official regional stores. It accepts a retailer image only when the
complete model code matches and the image comes from the reviewed delivery
host. Two explicit case fields from a separate watch source now map to the
existing review workbook columns, while unverified warranty and origin text
remain for staff review.

The image fallback matched five live models, and a sample image passed the
file-quality check. A live Caterpillar record produced two mapped case fields.
Twenty-one focused source and mapping tests passed. The changes were deployed
with backups and the affected discovery jobs were resumed. Final product
creation still requires workbook approval; image processing, attachment, and
publication remain separate steps and were not verified by this release.

## 2026-10-27 — Additional official watch-image route deployed

The connector reads one official watch site's product record and
accepts images only when its model identifier and brand match the requested
watch. It keeps the product's own gallery and excludes unrelated page images.
Two live models returned valid image files, while a model absent from that
regional catalog was correctly left unresolved. Focused tests passed. After
a backed-up deployment, the administrator's last image fallback for this
brand was changed to the reviewed official route. Two existing review jobs
then found exact product images without creating new jobs; one raw file had
downloaded at the first post-requeue check. Photoshop processing, storefront
attachment and publication are still pending.

## 2026-10-27 — Source audit follow-up

Read-only checks of several administrator-selected fallback sites found an
expired certificate, unavailable product catalog endpoints, and a current
catalog unrelated to watches. The audit records which routes are unverified
so they are not described as working image or data sources. Existing source
priorities and product records were left untouched during the audit. A later,
separately approved rollout installed a reviewed official image connector.

## 2026-10-27 — Aztorin image route deployed

The image connector serves an administrator-selected watch brand whose
official catalog has collection pages but no usable product sitemap. It
matches the exact model on a collection card and product page, then keeps
only full-size gallery images belonging to that product. Three live models
from separate collections returned exact matches and valid first images; one
model's main and gallery images both passed file checks. Focused regression
tests passed. Following a backed-up deployment, the installed worker returned
two matching images for the sampled model and validated the first file.
Administrator source priorities were preserved. Product data, Photoshop
processing and publication were not tested.

## 2026-10-27 — Official watch-gallery route

The image engine can use an official watch catalog's public product search
when its product titles show color names rather than model codes. The route
requires an exact SKU and keeps only gallery files bearing that model code.
It also removes repeated image URLs before download. Two live model searches
returned matching images; one gallery's unrelated packaging photo was
excluded. Focused tests passed. After a backed-up deployment, both models
returned two valid images each through the installed route and the worker
remained healthy. Product-data extraction was not verified for this source.

## 2026-10-27 — Two administrator-selected source routes

An adapter now reads a retailer's visible brand listing and exact product
card when its sitemap cannot be used. Gallery images are tied to that card's
internal product identifier, and specifications come from its own labelled
panel. A second retailer uses the existing public product API with a visible
brand check, avoiding a slow generic catalog scan.

Live samples for the configured brands returned exact product facts and
validated images where the retailer supplied enough resolution. Some sampled
images were too small and remain subject to the existing source fallback.
The first retailer's public listing does not cover every older model, and
four other configured domains remain inaccessible to the bot's normal
requests. Focused regression tests passed. After a backed-up deployment, live
checks of both installed routes found exact product facts and qualifying
images, and the existing worker was running. Historical catalog coverage,
manual approval and publication remain separate verification steps.

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

## 2026-10-27 — Fair catalog discovery across brands

When a large workbook contains several brands, older references from one brand could occupy every scheduled discovery slot. The catalog worker now rotates those slots across waiting brands while retaining the original order within each brand. It continues to prioritize approved drafts and due source retries.

Thirty-one catalog tests passed with a temporary database. After a backed-up production deployment, the first scheduled run advanced two brands in the same cycle. A reference from the previously waiting brand produced an editable review row with specifications from its configured source. Product creation still waits for review and approval of the combined workbook; image processing and publication were not part of this validation.

## 2026-10-27 — Catalog review queue fix deployed

A large product workbook can remain unsent when a few reference sites fail repeatedly while other models are still being researched. The catalog update gives due retries a place in each processing cycle and, after three unsuccessful source searches, keeps the affected model as an editable row in the original combined workbook. Staff can complete that row's specifications and return the same workbook for the existing approval step. Draft creation and publication remain subject to their separate checks.

Thirty catalog tests passed with a temporary database, including retry fairness, a combined workbook with a manual row, and the absence of product creation before approval. The production worker resumed after a backed-up deployment, and its first run processed a due retry alongside new references. The complete workbook, Telegram delivery, and resulting product imports still await the remaining source checks and staff approval.

## 2026-10-27 — Bounded cache for empty source catalogs

A source with a complete but empty product sitemap was being read again for every unmatched model in a large workbook. The image and data source engine now reuses that specific empty result for five minutes. Incomplete or failed sitemap requests still trigger their normal retry path, and non-empty catalogs keep their existing cache lifetime.

Thirty-five connector tests passed. After a backed-up production deployment, a live source lookup took about 39 seconds on its first read and returned immediately from cache on a second read. This confirms less repeated work for that fallback; completion of the workbook, product creation, image editing and publication remain subject to their existing steps.

## 2026-10-27 — Faster reference checks on a configured watch source

A configured retailer's brand listing was downloaded again for every model in a large review batch. The source connector now reuses a valid list of product links for ten minutes while checking each model against its own product page. Empty listings and source failures are not cached, so a temporary problem can still be retried. This speeds repeated checks without broadening the source's catalog coverage or changing the approval workflow.

Fifty-three focused connector tests passed. After a backed-up deployment, a read-only production check found 17 allowed links and read the same listing from cache on a second request. The scheduled worker and image API were running with a healthy worker. Product import, Photoshop processing and publication were not part of this source-only check.

## 2026-10-27 — Combined workbook progress in the bot

The bot previously directed operators to a status command that was not registered. Authorized operators can now use that command in private chat to see the existing image pipeline dashboard. It also shows how many rows of each pending combined workbook are ready, queued, being retried or need manual specifications. This makes the reason for a delayed workbook visible without splitting the review into many files.

Two focused progress tests passed. After a backed-up deployment, the running bot registered the command and rendered counts from the live queue without sending a test message. The pending workbooks still have source checks outstanding; this release did not approve data, create products or publish them.

## 2026-10-27 — Recovering a watch missing from a source sitemap

A configured watch retailer listed a model on its public brand collection but omitted it from the product sitemap. The catalog connector now checks that collection when the sitemap has no exact match, then verifies the individual product page. A narrowly scoped image check also accepts a small, valid compressed WebP from that reviewed source while retaining resolution and duplicate checks.

Fifty-six focused tests passed. On the running production worker, the affected model gained an editable review row and one validated raw image tied to its existing job. The image API returned healthy after the backed-up deployment. Product creation, Photoshop editing and publication still require the normal review and image steps.

## 2026-10-27 — Clearer catalog source failures

When several configured reference sites were unavailable, catalog jobs reported only a generic system error. They now record the affected source domains and short failure codes while keeping the existing retries and editable manual row after repeated failures. This makes it easier to decide whether to wait or fill in the specifications in the combined workbook.

Thirty-two catalog tests passed. After a backed-up production deployment, an actual retry recorded specific source failures and the existing worker and image API remained healthy. No product was approved, imported or published by this diagnostic change.

## 2026-10-27 — More exact watch data recovered for review

A configured retailer labeled one watch brand in both Persian and English. Its pages had exact model SKUs, but the source check interpreted that label as a different brand and discarded the product data. The source profile now recognizes that retailer's reviewed label for the affected brand while retaining exact model and brand checks.

Twenty-one focused tests passed. A live source check found nine previously rejected references with exact product pages and structured specifications. After a backed-up deployment, those same nine jobs produced editable review files in the existing combined intake. The source's sample image was below the configured resolution threshold, so it was not accepted as an image source. The workbook still needs operator approval; no products were created or published.

## 2026-10-27 — Clean supplier photos for a watch review batch

A configured image source had exact product photos, but each photo carried the same supplier logo below the watch. The image pipeline now removes that verified footer for the affected brand and rejects an unfamiliar footer for review. Other image sources keep their existing quality checks.

Twenty-four focused tests passed. After a backed-up deployment, three live model matches each produced a clean, validated image saved under its existing job. The image worker and intake service were running. The product workbooks still need approval and the photos still need the normal manual editing step; this update did not create or publish products.

## 2026-10-27 — Image downloads follow workbook readiness

Photo prefetch previously sorted new imports ahead of older products whose editable workbooks were already ready. It now gives those reviewable rows the first turn, then keeps the recent-import order within each stage. Retry timers, source checks and the operator's approval step remain in place.

Thirty-two focused catalog tests passed. After a backed-up deployment, the live selector placed twelve due workbook rows ahead of unprepared references. The image service and worker were running with a healthy heartbeat. Existing jobs for two watch brands were retried through the same queue, and review rows began to appear. The remaining draft products had categories but no featured images, so this release did not publish any product. Two processed images remain under an identity check because their hashes were already linked to other products.

## 2026-10-27 — Publish approved drafts with unique featured images

An exact duplicate in an optional gallery image previously stopped the entire image job, even when the featured image belonged uniquely to the approved product. The worker now skips and records that gallery duplicate while retaining the strict identity check for featured images. This lets complete, approved drafts continue through media SEO, attachment and publication without reusing another product's gallery photo.

Thirty-nine worker tests passed. In a backed-up production run, two previously held jobs each skipped one duplicate gallery photo, uploaded their unique featured image and remaining gallery, completed product SEO, and recorded successful publication. The scheduled worker was restored after the controlled run.

## 2026-10-27 — Ready image returns get the first turn

The image worker previously waited for catalog and source searches before uploading processed photos. A slow source could delay an approved product even when its edited image was already back from the desktop. The existing worker now checks ready uploads and attachments first, then runs its normal catalog and discovery work. It retains the later pass for jobs that become ready during the same run.

Forty worker tests passed, including a stage-order check. The backed-up production installation matched the reviewed source hash; the running worker had a fresh heartbeat and the image API responded successfully. No additional product was published in this deployment because the remaining approved drafts still need their manually edited images returned from the desktop.

## 2026-10-27 — Keep desktop photo delivery moving past filename conflicts

The desktop image queue could stall when its first few jobs shared filenames with photos already delivered for earlier reviews. The service still holds those ambiguous jobs, but now scans the rest of the ready queue for safe work. This lets other approved images reach the existing manual editing workflow without risking an attachment to the wrong product.

All 27 bridge integration tests passed, including the collision case. After a backed-up deployment, the live image API and worker were running and a ten-job desktop poll returned ten eligible jobs where the same poll had previously stopped at six. Separately, two approved drafts with individually reviewed featured photos completed the existing image and publication pipeline. Other drafts still await manual photo editing; the remaining filename conflicts require exact ownership review.

## 2026-10-27 — Recognize identical photos with different JPEG metadata

Some approved photo jobs were held because an earlier desktop handoff and the approved product held different JPEG files, even though the visible pixels were identical. The existing reuse step now accepts that exact pixel match after verifying both files and their ownership. Different views remain held for review.

Forty-eight reuse and worker tests passed. A read-only check of six live photo pairs accepted four exact matches and held two different views. After a backed-up deployment, the image worker and API were healthy. The pending products still require the operator's Photoshop output and return before publication; this update did not publish another product.

## 2026-10-28 — Clear image jobs already completed on the storefront

Some older image jobs still waited for desktop editing after their products had already gained a featured image, a complete gallery, and published status. The existing worker now checks the current product identity and image state before closing those jobs, while retaining incomplete galleries and missing images for the normal workflow. A temporary lookup failure leaves the job untouched.

The 42-test worker suite passed. After a backed-up deployment, a live readback closed 19 previously stale jobs and retained 19 incomplete ones. No product, media item, or approval was changed by this reconciliation. New products still require the reviewed workbook and the existing manual photo workflow before publication.
