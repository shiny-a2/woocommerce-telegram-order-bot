# Windows image handoff compatibility — 2026-10-01

The product image pipeline now gives the Windows editing agent filenames it can accept even when a product reference contains spaces, slashes or non-Latin characters. Safe existing filenames remain unchanged. A deterministic suffix prevents references that normalize similarly from overwriting each other.

Historical blocked items were checked against their recorded file hashes and resumed using the same product and job identity. The manual Photoshop approval step remains in place. The change was validated with agent/API integration tests and live handoff receipts.
