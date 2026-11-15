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

Company-group binding, a genuine file exercise and real database failure tests
remain pending. This is preview deployment evidence, not acceptance of automatic
price, stock, supplier-order or financial operations.
