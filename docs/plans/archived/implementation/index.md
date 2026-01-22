# pause-monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a real-time macOS system health monitor that identifies the root cause of intermittent system pauses through multi-factor stress detection, adaptive sampling, and automated forensics capture.

**Architecture:** A daemon continuously streams metrics from `powermetrics` (privileged), calculates a composite stress score, and stores samples in SQLite. When stress exceeds thresholds, sampling intensifies. When pauses are detected (via monotonic clock drift), forensics captures (spindump, tailspin, logs) are triggered. A Textual TUI provides real-time visualization; CLI commands enable querying history.

**Tech Stack:** Python 3.11+, SQLite (WAL mode), powermetrics (macOS), Textual (TUI), Click (CLI), structlog (logging), tomlkit (config)

**Source Design:** `docs/plans/2026-01-20-pause-monitor-design.md`

---

## Plan Structure

This implementation plan has been split into 7 parts for manageability:

| Part | File | Phases | Tasks | Description |
|------|------|--------|-------|-------------|
| 1 | [01-foundation.md](./01-foundation.md) | 1-2 | 1-7 | Config + Stress scoring |
| 2 | [02-storage.md](./02-storage.md) | 3-4 | 8-11 | SQLite schema + CRUD |
| 3 | [03-collection.md](./03-collection.md) | 5-6 | 12-17 | Metrics + Pause detection |
| 4 | [04-response.md](./04-response.md) | 7-8 | 18-20 | Forensics + Notifications |
| 5 | [05-daemon.md](./05-daemon.md) | 9 | 21-24 | Core daemon loop |
| 6 | [06-interface.md](./06-interface.md) | 10-12 | 25-32 | TUI + CLI + Install |
| 7 | [07-integration.md](./07-integration.md) | 13 | 33-36 | PID file + Tests + Docs |

**Full plan:** [00-full-plan.md](./00-full-plan.md) (original combined document)

---

## Dependency Graph

```
Part 1 (Foundation) ─────────────────────────────────────────┐
     │                                                       │
Part 2 (Storage) ───────────────────────────┐                │
     │                                      │                │
Part 3 (Collection) ────────────────────────┼────────────────┤
     │                                      │                │
Part 4 (Response) ──────────────────────────┼────────────────┤
     │                                      │                │
     └──────────────────────────────────────┴──→ Part 5 (Daemon)
                                            │         │
                                            │         │
                                            ├──→ Part 6 (Interface)
                                            │         │
                                            │         │
                                            └──→ Part 7 (Integration)
```

---

## Recommended Execution Order

1. **Start with Part 1** - Foundation has no dependencies
2. **Then Part 2** - Storage depends only on Part 1
3. **Parts 3 & 4 can be done in parallel** - Both only need Parts 1-2
4. **Part 5 after 3 & 4** - Daemon integrates all previous work
5. **Part 6 after 5** - Interface needs the daemon
6. **Part 7 last** - Integration tests everything

---

## Key Validation Fixes Applied

The plan has been validated and the following issues were fixed:

### Blockers Fixed
- Task ordering: storage.py created before conftest.py (conftest imports storage)
- Daemon `_run_loop`: Implemented actual powermetrics streaming loop
- Missing `import os` in install command: Added to Task 31

### High Priority Fixed
- `LegacyTimers` added to launchd plist for proper background scheduling
- Hysteresis added to SamplePolicy (elevate at 30, de-elevate at 20)
- TUI database connection: Added `_conn`, `on_mount`, `on_unmount`

### Medium Priority Fixed
- Plist parsing: Uses NUL byte separator (not XML header detection)

---

## Quick Reference

| Module | Purpose | Part |
|--------|---------|------|
| `config.py` | Configuration dataclasses | 1 |
| `stress.py` | StressBreakdown + calculation | 1 |
| `storage.py` | SQLite schema + CRUD | 2 |
| `collector.py` | Powermetrics + system metrics | 3 |
| `sleepwake.py` | Sleep/wake + pause detection | 3 |
| `forensics.py` | Spindump, tailspin, logs | 4 |
| `notifications.py` | macOS notification center | 4 |
| `daemon.py` | Main sampling loop | 5 |
| `tui/` | Textual dashboard | 6 |
| `cli.py` | Click commands | 6 |
