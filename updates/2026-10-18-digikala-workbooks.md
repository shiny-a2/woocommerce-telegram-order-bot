# Digikala workbook tools — 2026-10-18

The stock and price export tools now handle a mixed-brand operator workbook
without stopping at its header or unrelated brands. Operators see how many
out-of-scope rows were omitted, and an invalid price workbook is reported at
upload time. This makes both export flows usable again while keeping each
tool's existing brand and warranty rules.

Validation used representative workbooks and an in-memory run against current
operator exports. No seller-side upload was made during validation.
