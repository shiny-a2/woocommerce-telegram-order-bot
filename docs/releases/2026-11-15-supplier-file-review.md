# Supplier documents with traceable review

Prepared November 15, 2026; production deployment and activation are pending.

Supplier documents often put dealer cost beside consumer price, use a barcode
instead of a model reference, hide filtered rows, or present several tables on
one sheet. Treating those files as a standard price list can misidentify products
or mistake missing data for unavailable stock.

The prepared company-bound readers now support legacy XLS and XLSX review,
including multiple sheets and merged price cells. Each candidate retains its
source cell and separate price, cost and quantity evidence. Duplicate models,
blank or negative quantities, formula caches and foreign layouts stay visible.
No formulas or external workbook links are executed. Repeated files received
from different companies require ownership review.

When the file's purpose is unclear, the assistant asks the company politely in
a reply to that document. Only authorized current members in that company group
can answer, and the answer stays bound to the exact question and receipt.
The management topic receives the answer. Neither company answers nor manager
clarifications authorize a catalog write. Timeouts do not trigger duplicate
question sends, and private chats remain outside the prepared access boundary.

An owner-confirmed stock-list label describes the listed items only; omitted
products never become unavailable automatically. Explicit quantities take
precedence. A selected rounded retail column is kept separate from dealer cost
and the unrounded comparison amount. Date and currency still need validation.

Validation includes 152 passing synthetic checks: 120 supplier/access/order tests
and 32 existing catalog-consumer tests. Thirty-two real Excel samples were
replayed locally; one foreign-layout duplicate remained quarantined. An
illustrated PDF was inspected separately. Raw commercial files and extracted
data are excluded from publication. PDF extraction is not an automatic import;
oversized documents need splitting before the standard bot download path.

This release prepares review, not autonomous pricing, inventory, customer-order
status changes or accounting entries. Real group delivery, deployment, company
identity and acceptance remain separate operating checks. Existing catalog and
supplier-sync ownership is preserved.
