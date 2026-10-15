# Clearer handling of source access interruptions

The product data and image source engine now recognizes when a supplier site
returns a waiting page in place of a catalog response. It records a retryable
source interruption instead of treating the product as missing. This improves
diagnostics and avoids false absence decisions while keeping the normal
review workflow unchanged. It does not imply that restricted sites are now
accessible or that uploads were tested end to end.
