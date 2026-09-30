# Product and image workflow reliability

Extended an existing commerce automation pipeline with persistent reviewed-reference intake and recoverable image processing. Keep human spreadsheet review and manual desktop photo processing in the workflow.

Repeated submissions, concurrent work and partial failures preserve product and image identity. Improvements include retry handling, gallery completeness, explicit processing state, health visibility and controlled publication checks.

Validation included actual Windows process/SQLite tests, real HTTP image handoff, a separate WordPress/WooCommerce database, and a real public-source-to-draft integration with connection failure and restart recovery. Desktop operator acceptance and a production-host reboot are not claimed as completed tests.

Private source, customer data, credentials, server addresses and operational evidence remain private. This update is a sanitized engineering summary, not a production source mirror.

Live verification follow-up: bound discovery batches so subsequent processing can proceed promptly, and derive desktop availability from recent contact rather than a stale stored flag.

Incremental processing update: prioritize products missing their featured image and process new-reference discovery after the image stages. Verified two separate operator batches through the actual local agent API, preserving waiting items and preventing duplicate returns. Photoshop remains manually triggered; owner desktop end-to-end acceptance is not asserted.

Operator Excel selections now precede missing-featured products and gallery completion. New reference-source sites receive separate editable mapping notebooks; reviewed, approved corrections apply only to that site. Repeat submissions reuse product and image jobs.
