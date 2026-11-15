# Supplier files from review to traceable updates

The workflow now carries confirmed supplier-file fields into an exact
catalog preview. Managers see separate counts for proposed changes, held rows,
completed updates, rejected changes and uncertain outcomes. Downloadable reports
show proposed values and the receipts from earlier executions.

Catalog changes require matching product ownership, a current reviewed state,
confirmed source validity and bounded price changes. Duplicate or ambiguous
references stay held. A partial list cannot make omitted products unavailable.
If a request times out, the workflow checks its receipt rather than blindly
resending it. Existing inventory ownership remains respected.

Automatic execution is an explicit company-level option with independent write
controls. A newly added group is only a setup candidate until configured;
membership does not grant internal management access. Financial posting and
automatic order-status changes remain outside this implementation.

Validation includes 155 local supplier/access/order tests, 32 existing catalog
tests, 63 isolated adapter checks and 61 inventory/company-resolution checks. A
synthetic producer/consumer fixture verifies the shared API shape. These tests do
not establish production database concurrency, external delivery or acceptance.
A controlled real-company exercise is still pending.

This note publishes behavior and validation only. Private source, commercial
files, operational configuration and customer data are not included.

The owner-approved preview has now been installed with write and supplier-order
sending controls disabled. The bot restarted successfully, its source hashes
matched the reviewed release, and the backup passed integrity checks. Live
read-only snapshots respected existing inventory ownership; requests without
credentials or a configured company were refused. Private/public command menus
were cleared and management commands were scoped to authorized administrators.

The company group is now bound after verifying the owner-initiated membership
event and current membership. A genuine file exercise remains pending. This is preview deployment evidence, not acceptance of automatic
price, stock, supplier-order or financial operations.


## Installed choice-question update

Ambiguous files are classified through two or three buttons per question: first
product list or financial document, then price, available stock or both. The
company answers in the original file reply. Repeated and cross-group choices
cannot overwrite the recorded answer. Managers answer one missing fact at a time;
price/date confirmations include a hold option, and stale buttons reopen the
current question without changing the recorded parameters. Clarification does
not grant permission to update the store.

Bot version 2026.11.16.2 passed 165 supplier/access/order tests and
32 catalog-consumer tests. The companion adapter also passed 34 real, isolated
WordPress/WooCommerce/MariaDB assertions with synthetic products, including
process termination, concurrent locks and rollback. This exposed and fixed a
per-product cache problem during receipt recovery. The production cache plugin
and full plugin stack were not covered. No test message or real order was sent.

The owner-approved question update and companion recovery correction are now
installed with matching source hashes and verified backups. The bot restarted
with one worker; the sampled startup log had no supplier or traceback error.
Company membership, scoped management menus, private-chat denial and read-only
site access were checked. All write controls remain disabled. A company-file
trial and inventory ownership agreement are still required before activation. Financial posting and automatic order
status changes are not implemented.


## Prepared multi-sheet intake recovery

A genuine company workbook exposed a gap in the generic reader: multiple sheets
were held, and answering the document-purpose question did not provide a way to
resume that saved file. The prepared follow-up reads the observed supplier layout
across sheets and keeps internal item codes, references, titles and amounts
distinct. A stock-only answer never authorizes price changes. Conflicting model
identifiers and unexpected layouts remain visible for review.

A manager can reopen the original file after its reader is available. The
document hash, existing answers and concurrent decisions are protected. Dates
that have expired lead to a current-validity decision or a hold. No file is treated
as current merely because it was downloaded again.

Validation covers 180 supplier/access/order tests and 32 separate catalog tests,
plus a replay of the actual workbook and read-only catalog responses. The saved
answer and row accounting were preserved, and no site write was performed. The
new source is prepared as 2026.11.16.3; installation and resuming the saved batch
are pending approval. The installed 2026.11.16.2 preview remains write-disabled.
Private company files, product references and operational identifiers are not
published here.
