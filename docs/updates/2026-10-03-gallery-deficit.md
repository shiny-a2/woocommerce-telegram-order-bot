# Gallery work sized to the remaining images

The product image worker now checks the store's current gallery count before searching and again before downloading. A product with one gallery image prepares one additional image; a complete gallery is skipped. The live product identity is verified before work proceeds, so a manually changed product cannot silently receive a stale job's images.

This reduces unnecessary desktop photo processing while keeping the existing review and attachment workflow. Isolated worker regressions and read-only checks against live product records passed. Exact duplicate hashes remain protected; visually similar legacy photos with different bytes still require review.
