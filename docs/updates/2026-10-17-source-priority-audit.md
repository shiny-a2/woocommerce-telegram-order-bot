# Source priority and coverage audit

The catalog pipeline now has a current inventory of administrator-configured image and specification sources by brand. The audit distinguishes configured domains from scrapers that returned a verified, exact-reference result, making missing coverage visible before new spreadsheet batches are approved.

Two specification-source priorities were corrected only after live reference checks. A full priority search confirmed that the engine moved past two misses and accepted the next exact result; a second brand returned structured specifications from its newly configured first source. Existing source order, human spreadsheet approval, and image publication controls remain in place.

Source lists alone do not establish end-to-end image processing. Brands without a verified specification source remain queued for review rather than being published with guessed data.

A retailer-specific specification layout is now supported through a narrowly scoped parser and a deterministic model URL. Two live references returned exact matches and structured specifications; image discovery for this brand remains a separate task.

The scheduled worker now keeps each background image-search batch smaller so a newly submitted spreadsheet reaches the catalog phase sooner, while preserving the established source order and retry states.
