# Product photo editing during workbook review — 2026-10-18

Validated catalog photos can now reach the existing Windows editing workflow while the product workbook is still being corrected. Edited files return to the bot server under the same tracked job and are held there until the reviewed workbook is approved. This shortens the wait between data approval and finished product imagery while preserving the human review and publish gates.

The update also prevents a source-enrichment pass from changing the raw-image manifest after a desktop handoff. The bridge reports counts for images sent to Windows and returned during workbook review. Loopback integration tests cover a full early photo round trip, retries, and draft binding during an in-flight return. Live Photoshop output and storefront publication still require their normal operator steps and verification.
