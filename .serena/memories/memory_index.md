# Memory Index

**Last audited:** 2026-01-30

| Memory | What It Answers |
|--------|-----------------|
| `session_context` | **Read first.** Philosophy + where to find everything |
| `design_spec` | What should exist? (canonical spec from design docs) |
| `implementation_guide` | What does exist and how does it work? |
| `unimplemented_features` | What's missing? (gaps between design and code) |
| `insights` | What patterns and gotchas have we learned? |
| `process_tracker_design` | How does per-process tracking work? |
| `reboot_data_correlation_design` | How to correlate data across reboots? |
| `libproc_and_iokit_research` | macOS API documentation for libproc/IOKit |
| `architecture_postmortem` | Historical context on data architecture decisions |

## Project Status

**Core system: FULLY IMPLEMENTED**

- LibprocCollector collecting per-process metrics at 5Hz
- ProcessTracker managing event lifecycle with snapshots
- Storage at schema v13 with full MetricValue support
- TUI single-screen dashboard with real-time socket streaming
- Forensics capture (spindump, tailspin, logs) on band entry

**Remaining work:** Minor nice-to-have features. See `unimplemented_features`.

## Focus

System is production-ready. Maintenance mode.
