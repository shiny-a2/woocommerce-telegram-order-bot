# Smaller public catalogue pages

The competitor collector now requests the catalogue fields its readers use from a verified public Store API. Large descriptions and unrelated extension data no longer travel with every page. Product identifiers, brand evidence, price units, explicit stock flags, links and images remain available.

The scoped collector update is installed. Other sources retain their existing request contract, and pagination, retry limits, price checks and previous observations remain intact. A fresh source capture is in progress; full catalogue receipt is not yet verified.

Validation included 104 isolated competitor tests. Two native public page checks returned matching product IDs and identical parsed report fields while reducing response bytes by approximately 53% and 77%. These page samples do not establish whole-catalogue completion or a general latency guarantee. Existing customer-message and order-processing components were not restarted.
