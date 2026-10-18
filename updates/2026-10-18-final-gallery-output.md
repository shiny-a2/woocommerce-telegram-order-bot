# Complete gallery return for multi-photo products — 2026-10-18

The Windows photo return path now accepts the final gallery image produced from a set of numbered raw photos. Previously, a three-photo source could produce four edited files, but the last gallery image was rejected because it did not share an exact filename with a raw photo. This could hold an entire manually approved upload batch.

The server validates the output position against the same tracked product-image job, rejects extra or ambiguous filenames, and keeps the existing file-hash checks. Integration tests cover the desktop upload round trip and repeat submission. A real Photoshop run for the reported product is still pending.
