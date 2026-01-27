# ProcessTracker Design

> **Status:** SUPERSEDED by `docs/plans/2026-01-25-per-process-band-tracking-design.md`

This memory contains early exploration notes. The final design uses event-based tracking with:
- Binary states: NORMAL (below threshold) vs BAD (at/above threshold)
- Events created when process crosses threshold
- Snapshots: entry, peak (on event row), checkpoints, exit
- Same ProcessScore schema throughout

See the design document for complete details.