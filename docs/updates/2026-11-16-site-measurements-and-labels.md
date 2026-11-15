# Product review units and source labels

Product review workbooks now write watch dimensions and weights in the same
compact `mm` and `g` format used by the storefront. When an older workbook is
returned, its equivalent Persian unit spelling is normalized during import.
This keeps the operator's existing review and approval flow usable.

The catalog mapper also recognizes explicit source labels for case size,
style, clasp, glass material and a few other existing product attributes.
It uses the store's established terms. Ambiguous descriptions, missing units,
warranty and origin remain for human review.

The update was checked against retained product facts and live storefront
examples from multiple brands. One hundred and one focused tests passed.
The bot and image worker restarted successfully, and an existing legacy
workbook was parsed with the corrected unit format. No product was imported
or published by this release.
