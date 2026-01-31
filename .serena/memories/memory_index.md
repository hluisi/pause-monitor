# Memory Index

**Last audited:** 2026-01-31

## About Rogue Hunter

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes negatively affecting system performance. It scores all processes on four dimensions of rogue behavior (blocking, contention, pressure, efficiency) and tracks those that cross configurable thresholds.

## Core Memories

| Memory | What It Answers |
|--------|-----------------|
| `00_start_here` | Philosophy + where to find everything |
| `design_spec` | What should exist? (canonical spec from design docs) |
| `implementation_guide` | What does exist and how does it work? |
| `unimplemented_features` | What's missing? (gaps between design and code) |
| `insights` | What patterns and gotchas have we learned? |

## Design Memories

| Memory | What It Answers |
|--------|-----------------|
| `process_tracker_design` | How does per-process tracking work? |
| `reboot_data_correlation_design` | How to correlate data across reboots? |
| `data_schema` | Canonical ProcessScore schema — THE data format used everywhere |
| `tui_layout` | Visual layout, component hierarchy, CSS classes for the TUI |
| `block_format` | How to structure skill blocks (for skill authors) |
| `refactoring_discussion_2026-01-31` | Pending improvements: scoring, thresholds, MetricValue evaluation |

## Research Memories

| Memory | What It Answers |
|--------|-----------------|
| `libproc_and_iokit_research` | macOS API documentation for libproc/IOKit |
| `macos_privilege_escalation` | How to run privileged commands (sudoers, helpers) |
| `spindump_vs_tailspin` | Which diagnostic tool to use when |
| `tailspin_usage` | Complete tailspin command reference and integration |

## Historical Memories

| Memory | What It Answers |
|--------|-----------------|
| `architecture_postmortem` | Historical context on data architecture decisions |
| `config_audit_2026` | Audit of all config options (valid, unused, dead) |

## Meta Memories

| Memory | What It Answers |
|--------|-----------------|
| `subagent_best_practices` | How to spawn and use subagents in skills |

## Issue Finder

The `/issue-finder` skill maintains its own memory system:

| Memory | Purpose |
|--------|---------|
| `issue-finder-index` | Master index of all issues (active/resolved) |
| `issue-finder-ISS-*` | Detailed investigation notes for specific issues |

**Current issues with detailed memories:** ISS-002 (resolved), ISS-008 (resolved), ISS-009 (resolved), ISS-014 (active)

---

## Project Status

**Core system: FULLY IMPLEMENTED**

- LibprocCollector scoring ALL processes on 4 dimensions (blocking/contention/pressure/efficiency)
- Top N rogues always displayed (TUI never empty)
- ProcessTracker managing event lifecycle with entry/exit/checkpoint snapshots
- Storage at schema v14 with full MetricValue support (current/low/high)
- TUI single-screen dashboard with real-time socket streaming + auto-reconnect
- Forensics capture (tailspin + logs) on band entry with debouncing
- Daemon manages tailspin lifecycle (enable on start, disable on stop)
- Bidirectional socket protocol (TUI can send logs to daemon)

**Pending evaluation:** See `refactoring_discussion_2026-01-31` for threshold tuning and MetricValue evaluation.

## Key Values

| Setting | Value |
|---------|-------|
| Sample rate | 3 Hz (~0.333s interval) |
| Ring buffer | 60 samples (~20s history) |
| Schema version | 13 |
| Tracking threshold | score ≥ 40 (elevated band) |
| Forensics threshold | score ≥ 50 (high band) |
| Forensics debounce | 2 seconds |

## Focus

Core rogue detection is production-ready. Evaluating threshold tuning and data structure simplification based on real-world usage.

## Audit Notes (2026-01-30)

- Memory index updated to include all 23 memories
- Issue-finder memories documented as separate category
- No stubs found in codebase
- TUI has hardcoded thresholds that should use config (medium priority)
- `config show` incomplete (low priority)
