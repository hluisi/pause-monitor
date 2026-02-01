# Project Overview

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes negatively affecting system performance.

## Purpose

Track down intermittent macOS system pauses by continuously monitoring all running processes and scoring them on four dimensions of "rogue behavior":

| Category | Weight | What It Detects |
|----------|--------|-----------------|
| **Blocking** | 40% | I/O bottlenecks, memory thrashing, disk saturation |
| **Contention** | 30% | CPU fighting, scheduler pressure, context switching |
| **Pressure** | 20% | Memory hogging, kernel overhead, excessive wakeups |
| **Efficiency** | 10% | Stalled pipelines, thread proliferation |

When processes cross configurable thresholds, forensic data (tailspin, logs) is captured automatically.

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
| `rogue-hunter daemon` | Background sampler (5Hz) |
| `rogue-hunter tui` | Interactive dashboard |
| `rogue-hunter events` | Historical event queries |
| `rogue-hunter status` | Quick health check |

## Architecture Summary

- **Daemon** collects process metrics via libproc (no subprocess spawning)
- **ProcessTracker** creates events when processes cross thresholds
- **RingBuffer** maintains 30 seconds of pre-incident context
- **SocketServer** streams real-time data to TUI via Unix socket
- **SQLite** persists events with entry/peak/exit snapshots

## Project Type

Personal project — one developer + AI assistants. No external users, no backwards compatibility. Breaking changes are free.
