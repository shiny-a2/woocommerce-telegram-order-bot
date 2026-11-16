# Supplier import follow-up — prepared release

Completed bulk imports and supplier documents now feed the same product research
and image review workflow. Explicit availability, an approved brand and a second
exact catalog check are required before a missing reference enters that queue.
Unclear rows remain visible for review, and product creation retains its approval
step. Bulk-import intake can operate before a supplier chat is registered.

Scheduled follow-up runs on Saturday at 09:00 in the configured local timezone
when more than ten days have elapsed since a valid price or availability list.
Receipt of a valid list is tracked separately from applying its changes. Retries
preserve row progress; uncertain message delivery does not trigger blind resends.
Products still in stock but missing from a price list are reported without being
marked unavailable.

The isolated, network-blocked sandbox passed 239 supplier, access, order and
catalog tests. A separate cross-plugin WordPress/WooCommerce/MariaDB fixture
passed 55 checks. No real messages or purchases were created for these tests.
This is prepared source; production installation and activation remain pending.
