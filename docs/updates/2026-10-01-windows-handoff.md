# Windows image handoff reliability — 2026-10-01

The image pipeline now checks incoming filenames and raw-file availability before assigning work to the desktop editing agent. An invalid job is recorded for review, while valid jobs continue in priority order. This prevents a single malformed input from blocking an entire processing queue and preserves the existing manual editing and upload workflow.

Validation included a loopback API/agent integration test for queue progress and the image worker regression suite.
