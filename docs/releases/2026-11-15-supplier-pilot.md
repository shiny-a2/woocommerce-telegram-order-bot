# Reviewed supplier coordination and closed group access

Current follow-up: [supplier document review](2026-11-15-supplier-file-review.md)
adds sample-derived readers and company questions, with 152 passing synthetic
checks. The sections below record the earlier preparation milestones.

Prepared on November 15, 2026. Source implementation and local validation are
complete for the first pilot; production deployment and company activation are
pending. The pilot is disabled by default.

An order card could keep its old generic supplier label after the underlying
company resolver was corrected, because the order itself had not changed.
A bounded refresh now revisits recent, already-posted cards and edits the same
message. Failed reads preserve the existing financial information and remain
eligible for retry. A read-only reconstruction of the reported stale card changed
only the company/source lines; its money lines were unchanged. Live delivery was
not tested or performed.

The supplier pilot separates company representatives from internal staff and
management tools. Private messages and unknown groups are rejected before
capture or command handling; outbound transports also block private destinations.
Management menus require an explicitly configured administrators' group.

Paid supplier-order lines enter a durable manager-review queue. An approved
request sends a product photo, reference, quantity and tracking token without
customer details. An uncertain send stops for reconciliation instead of risking
a duplicate request. Supplier acknowledgement and shipment are recorded separately
from customer delivery.

CSV/XLSX intake distinguishes stock, price and combined files, requires clear
currency, rejects ambiguous or executable cells, and detects repeated files and
references. Read-only previews flag unresolved company/product matches, physical
stock and large price differences. Missing rows in a partial list never make
other products unavailable. Price and stock reminders are independent review
requests. This phase does not write catalog prices or stock, change customer
order statuses, or use AI to authorize actions.

71 synthetic tests passed, covering the real application access boundary,
transport denial before HTTP, file validation, company isolation, duplicate-send
prevention, timeout recovery, notification failure, bounded card refresh, and
existing order/location/payment behavior. SQLite backup, integrity and indexed
queries were exercised on 10,000 synthetic actions and 2,000 file batches.
Import, syntax and whitespace checks passed. These checks do not establish
acceptance against an actual company file, live dispatch or shipping process.

The next milestone is agreement with one company on participation, file
format, price meaning, currency, stock coverage and shipment evidence, followed
by reviewed real operations. Automated website updates remain a later integration
requiring ownership checks, revision protection, explicit approvals and recovery.

## Prepared follow-up: forum review and catalog handoff

Management notices and approvals now support a selected forum topic. A configured
company group can allow all current human members while retaining separate
administrator permissions. Membership is checked before document reads and
supplier actions; departed members and failed checks cannot act.

Unclear files ask managers to identify price, stock, combined lists or invoices.
Currency answers are recorded and the original file hash is checked before
read-only parsing resumes. Unknown layouts and PDFs stay in format review;
PDF receipt does not imply OCR or invoice interpretation. Discount/invoice
columns are not silently treated as a retail price update.

An optional, disabled bridge sends confirmed missing references with approved
brands to the existing product-data/image review queue. It rechecks absence,
uses the existing deduplication key and preserves administrator data/image
approval. Review workbooks and image cards are routed to the management topic,
with paginated file results linking the corresponding jobs. It does not publish
products or invent facts for missing specifications.

The follow-up passes 123 synthetic checks: 91 supplier/access/order regressions
and 32 catalog-review consumer tests. Actual company files, group delivery,
scraper results and end-to-end publication remain unverified. Production
deployment and pilot activation are still pending.

Invoice, quantity, agreed discount, verified payments and balance reconciliation
are a documented next milestone. That phase first needs real document formats
and an identified accounting source of truth. No accounting entries, settlement
or money transfer automation is implemented in this preparation.
