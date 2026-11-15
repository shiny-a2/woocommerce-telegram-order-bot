# Supplier files from review to traceable updates

The prepared workflow now carries confirmed supplier-file fields into an exact
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
Deployment and a controlled real-company exercise are still pending.

This note publishes behavior and validation only. Private source, commercial
files, operational configuration and customer data are not included.
