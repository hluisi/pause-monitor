---
id: ref-project-overview
type: ref
domain: project
subject: project-overview
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [project_overview]
tags: []
related: []
sources: []
---

# Project Overview

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes consuming disproportionate system resources.

## Purpose

Track down intermittent macOS system pauses by continuously monitoring all running processes. The scoring system identifies processes that claim more than their fair share of any resource:

| Resource | What It Measures |
|----------|------------------|
| **CPU** | CPU time relative to fair share per active process |
| **GPU** | GPU time consumption |
| **Memory** | Resident memory footprint |
| **Disk** | I/O bytes read/written |
| **Wakeups** | Interrupt wakeups (power impact) |

### Disproportionate-Share Scoring (v18)

Instead of categorical scoring, the system calculates each process's **share** of system resources:

```
fair_share = 1.0 / active_processes  (e.g., 100 processes = 1% fair share)
cpu_share = process_cpu / (active_processes × per_core_fair_share)
```

The **disproportionality** is the maximum share across all resources. A process using 15% of CPU when fair share is 1% has 15× disproportionality.

Scores are assigned by band:
- **Low (0-29)**: Normal behavior
- **Medium (30-44)**: Slightly elevated
- **Elevated (45-59)**: Tracking begins, entry snapshots captured
- **High (60-79)**: Significant resource usage
- **Critical (80+)**: Forensics triggered automatically

When processes cross thresholds, forensic data (tailspin, logs) is captured automatically.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.14 |
| Package Manager | uv |
| TUI Framework | Textual |
| CLI Framework | Click |
| Database | SQLite (aiosqlite, WAL mode) |
| Logging | structlog |
| Configuration | TOML (tomlkit) |
| Data Collection | macOS libproc.dylib (ctypes) |

## Key Interfaces

| Interface | Purpose |
|-----------|---------|
| `rogue-hunter daemon` | Background sampler (3Hz) |
| `rogue-hunter tui` | Interactive dashboard |
| `rogue-hunter events` | Historical event queries |
| `rogue-hunter status` | Quick health check |

## Architecture Summary

- **Daemon** collects process metrics via libproc at 3Hz (no subprocess spawning)
- **ProcessTracker** creates events when processes cross tracking threshold (configurable via `bands.tracking_threshold`)
- **RingBuffer** maintains recent samples for context and sparklines
- **SocketServer** streams real-time data to TUI via Unix socket (push-based)
- **SQLite** persists events with entry/checkpoint/exit snapshots

## Project Type

Personal project — one developer + AI assistants. No external users, no backwards compatibility. Breaking changes are free.
