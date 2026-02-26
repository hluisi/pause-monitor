---
id: architecture-process-tracker
type: architecture
domain: project
subject: process-tracker
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [process_tracker_design]
tags: []
related: []
sources: []
---

# ProcessTracker Design

> **Status:** SUPERSEDED by `docs/plans/2026-01-25-per-process-band-tracking-design.md`

This memory contains early exploration notes. The final design uses event-based tracking with:
- Binary states: NORMAL (below threshold) vs ROGUE (at/above threshold)
- Events created when process crosses tracking threshold
- Snapshots: entry, peak (on event row), checkpoints, exit
- Same ProcessScore schema throughout

**Key insight:** ProcessTracker applies its own threshold for persistence, independent of what the collector shows in the TUI. This separation means:
- TUI always shows top N processes by score (real-time visibility)
- ProcessTracker only persists processes above `tracking_threshold` (forensic capture)

See the design document for complete details.
