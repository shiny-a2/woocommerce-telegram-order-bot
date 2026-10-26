# Public release notes

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
