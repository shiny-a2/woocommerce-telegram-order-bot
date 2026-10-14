# Reference-source verification: remaining batches

The remaining configured watch-reference domains were checked from the live
bot host in small groups, completing an initial sample of all 44 domains.
This is an inventory audit, not a claim that every site can currently supply
automatic product data or images: several sites restrict requests, and several
generic source settings do not match the site's current product routes.

One confirmed Citizen image source was repaired. Its exact product was
present under a root-level URL and a product-specific sitemap, while the old
generic route looked only under `/product/`. The existing source connector now
uses the verified sitemap route and validated a qualifying product image.
Source priorities, approval, image processing on Windows and publication
rules were unchanged. Remaining domains are tracked separately for targeted
route verification; an empty search result is not treated as proof that the
product does not exist.
