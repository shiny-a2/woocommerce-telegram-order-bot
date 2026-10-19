# Corrected workbook approval across mixed review states

An operator-edited product workbook could be blocked when a few rows had
exhausted automated source discovery, even though the operator supplied their
specifications. The approval flow now lets those explicitly reviewed rows
continue to draft import alongside ordinary pending rows, while preserving
workbook identity checks, repeat-safe processing, and separate publication
approval. Import failures still require review rather than automatic replay.
