# pause-monitor Design Document

**Date:** 2026-01-20
**Status:** Approved
**Author:** Hunter (with Claude)

## Overview

A comprehensive system health monitoring tool for macOS that tracks down intermittent system pauses. Unlike simple CPU monitors, it uses multi-factor stress detection to distinguish "busy but fine" from "system degraded."

## Goals

1. **Root cause identification** - Capture enough data during/after pauses to identify the culprit process
2. **Historical trending** - Track system behavior over days/weeks to spot patterns
3. **Real-time alerting** - Know when the system is under stress before it freezes

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Sampler    │───▶│   Storage    │◀───│     CLI      │       │
│  │   (daemon)   │    │   (SQLite)   │    │   Queries    │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                   ▲                                    │
│         │                   │                                    │
│         ▼                   │                                    │
│  ┌──────────────┐    ┌──────────────┐                           │
│  │   Forensics  │    │     TUI      │                           │
│  │  (on pause)  │    │  Dashboard   │                           │
│  └──────────────┘    └──────────────┘                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Responsibility |
|-----------|----------------|
| **Sampler** | Background daemon collecting metrics every 5s (or 1s when elevated). Uses `psutil` for low-overhead process/memory/I/O/network stats. macOS-specific calls for thermals. |
| **Storage** | SQLite database at `~/.local/share/pause-monitor/data.db`. Auto-prunes data older than 30 days. |
| **Forensics** | Triggered when a pause >2s is detected. Captures full process list, runs `spindump`, extracts system logs. |
| **CLI** | Commands like `pause-monitor status`, `pause-monitor history`, `pause-monitor events`. |
| **TUI** | Live dashboard showing current stats, recent events, trends. Built with `textual`. |

## Data Model

### SQLite Tables

```sql
-- Periodic samples (one row per sample interval)
samples (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL,           -- Unix timestamp with ms precision
    interval    REAL,           -- Actual seconds since last sample (detects pauses)
    cpu_pct     REAL,           -- System-wide CPU %
    mem_pct     REAL,           -- Memory pressure %
    mem_free    INTEGER,        -- Bytes free
    swap_used   INTEGER,        -- Bytes in swap
    io_read     INTEGER,        -- Bytes/sec read
    io_write    INTEGER,        -- Bytes/sec write
    net_sent    INTEGER,        -- Bytes/sec sent
    net_recv    INTEGER,        -- Bytes/sec received
    cpu_temp    REAL,           -- Celsius (null if unavailable)
    gpu_pct     REAL            -- GPU utilization %
)

-- Per-process snapshots (linked to samples, only for top consumers)
process_samples (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER REFERENCES samples(id),
    pid         INTEGER,
    name        TEXT,           -- Process name
    cpu_pct     REAL,
    mem_pct     REAL,
    io_read     INTEGER,        -- Bytes/sec
    io_write    INTEGER,
    is_suspect  BOOLEAN         -- Matches suspect list
)

-- Pause events (when interval > threshold)
events (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL,
    duration    REAL,           -- Seconds system was unresponsive
    spindump    TEXT,           -- Path to spindump file
    logs        TEXT,           -- Path to extracted logs
    notes       TEXT            -- User-added notes (optional)
)
```

### Storage Estimates (30 days)

- ~518K samples at 5s intervals = ~50 MB for `samples` table
- ~5 process rows per sample = ~250 MB for `process_samples`
- Events + forensics: ~5 MB per pause event
- **Total: ~300-500 MB typical**

### Auto-pruning

Daily job deletes samples older than 30 days, keeps event forensics for 90 days.

## Stress Detection

**Key insight:** CPU percentage is meaningless. What matters is *contention* - are processes waiting for resources?

### Stress Signals

| Signal | How to measure | Why it matters |
|--------|----------------|----------------|
| **Load vs cores** | `load_avg / core_count` | >1.0 means processes are queuing |
| **I/O wait** | `psutil.cpu_times().iowait` | Processes blocked on disk |
| **Memory pressure** | `memory_pressure` command | System is compressing/swapping |
| **Self-latency** | `actual_sleep - expected_sleep` | Direct measure of responsiveness |
| **Run queue depth** | `vm_stat` pageins/outs rate | Paging activity spike |

### Stress Score Calculation

```python
def calculate_stress_score():
    score = 0

    # Load average relative to cores (0-100 scale)
    load_ratio = load_avg_1min / core_count
    if load_ratio > 1.0:
        score += min(40, (load_ratio - 1.0) * 20)  # Max 40 points

    # I/O wait (0-100 scale)
    if io_wait_pct > 10:
        score += min(30, io_wait_pct)  # Max 30 points

    # Memory pressure (0-100 scale)
    if mem_free_pct < 20:
        score += (20 - mem_free_pct) * 2  # Max 40 points

    # Self-latency (did our own sleep lag?)
    if actual_interval > expected_interval * 1.5:
        score += 30  # We're already experiencing delays

    return score
```

### Adaptive Sampling

- **Normal mode:** 5s intervals
- **Elevated mode (stress > 30):** 1s intervals
- **Critical (stress > 60):** Preemptive snapshot capture

### Elevation Triggers

- Load average exceeds core count
- I/O wait exceeds 10%
- Memory pressure below 20% free
- Self-latency spike (sleep took 50% longer than expected)
- Suspect process exceeds 30% CPU

## Forensics Capture

When a pause is detected (interval > 2x expected):

1. **Immediate process snapshot** (~50ms) - Full ps output via psutil
2. **Trigger spindump** (~5-10s) - `sudo spindump -reveal -noProcessingWhileSampling`
3. **Extract system logs** (~1s) - Filter for errors, hangs, memory warnings

### Forensics Storage

```
~/.local/share/pause-monitor/events/<timestamp>/
├── processes.json      # Full process snapshot
├── spindump.txt        # Thread stacks (2-5 MB)
├── system.log          # Filtered system logs
└── summary.json        # Quick overview for TUI/CLI
```

### Log Extraction Query

```bash
log show --start "<pause_start>" --end "<pause_end>" \
    --predicate 'eventType == fault OR
                 messageType == error OR
                 subsystem CONTAINS "biome" OR
                 process == "kernel"' \
    --style compact
```

## TUI Dashboard

```
┌─ pause-monitor ──────────────────────────────────────────── 09:32:15 ─┐
│                                                                        │
│  SYSTEM HEALTH          STRESS: ██░░░░░░░░ 12%        Mode: Normal 5s │
│  ───────────────────────────────────────────────────────────────────── │
│  CPU:  ████████░░░░░░░░ 47%    Load: 3.4/16 cores                     │
│  Mem:  ████████████░░░░ 73%    Free: 34 GB                            │
│  I/O:  ██░░░░░░░░░░░░░░  8%    R: 12 MB/s  W: 45 MB/s                 │
│  Net:  ███░░░░░░░░░░░░░ 15%    ↓: 2.1 MB/s  ↑: 0.3 MB/s               │
│  Temp: ████░░░░░░░░░░░░ 52°C   GPU: 23%                               │
│                                                                        │
│  TOP PROCESSES                                               CPU  MEM  │
│  ───────────────────────────────────────────────────────────────────── │
│  claude -c                                                  106%  4.5% │
│  claude                                                      87%  0.5% │
│  ghostty                                                     24% 57.4% │
│  WindowServer                                                20%  0.2% │
│  Wispr Flow Helper                                            4%  0.1% │
│                                                                        │
│  RECENT EVENTS                                                         │
│  ───────────────────────────────────────────────────────────────────── │
│  ⚠ 09:10:13  PAUSE 74.3s  [biomesyncd suspected]         [View: Enter]│
│  ● 03:44:01  Elevated sampling triggered (I/O spike)                   │
│  ● Yesterday 22:15  PAUSE 12.1s  [BDLDaemon suspected]                 │
│                                                                        │
│  [q] Quit  [e] Events  [p] Processes  [h] History  [?] Help           │
└────────────────────────────────────────────────────────────────────────┘
```

### Views

- **Dashboard** (default) - System overview
- **Processes** - Full process list, sortable
- **Events** - Pause history with filters
- **History** - Charts of metrics over time

## CLI Interface

```
pause-monitor
├── daemon      # Run the background sampler
├── tui         # Launch interactive dashboard
├── status      # Quick health check (one-liner)
├── events      # List/inspect pause events
├── history     # Query historical data
├── config      # Manage configuration
├── install     # Set up launchd service
├── uninstall   # Remove launchd service
└── prune       # Manual data cleanup
```

### Example Usage

```bash
# Quick status check
$ pause-monitor status
✓ Healthy | Stress: 12% | Load: 3.4/16 | Mem: 73% | Last pause: 2h ago

# List recent pause events
$ pause-monitor events
ID  TIMESTAMP            DURATION  SUSPECT
3   2026-01-20 09:10:13  74.3s     biomesyncd (95% CPU before pause)
2   2026-01-19 22:15:01  12.1s     BDLDaemon (I/O spike)

# Inspect a specific event
$ pause-monitor events 3

# Show what was happening at a specific time
$ pause-monitor history --at "2026-01-20 09:08:55"
```

## Project Structure

```
pause-monitor/
├── pyproject.toml
├── CLAUDE.md
├── AGENTS.md
├── README.md
├── LICENSE
├── docs/
│   └── plans/
│       └── 2026-01-20-pause-monitor-design.md
├── src/
│   └── pause_monitor/
│       ├── __init__.py
│       ├── __main__.py      # Entry point
│       ├── cli.py           # CLI commands (click)
│       ├── daemon.py        # Background sampler
│       ├── collector.py     # Metrics collection
│       ├── storage.py       # SQLite operations
│       ├── forensics.py     # Pause event capture
│       ├── stress.py        # Stress score calculation
│       └── tui/
│           ├── __init__.py
│           ├── app.py       # Textual app
│           └── widgets.py   # Custom widgets
└── tests/
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `psutil` | Cross-platform process/system metrics |
| `textual` | Modern TUI framework |
| `rich` | Pretty CLI output |
| `click` | CLI framework |

## Installation

```bash
# Development
uv run pause-monitor daemon
uv run pause-monitor tui

# Install globally
uv tool install .

# Enable daemon
pause-monitor install
```

## Data Locations

- **Config:** `~/.config/pause-monitor/config.toml`
- **Database:** `~/.local/share/pause-monitor/data.db`
- **Events:** `~/.local/share/pause-monitor/events/`
- **Daemon log:** `~/.local/share/pause-monitor/daemon.log`

## Configuration

```toml
# ~/.config/pause-monitor/config.toml

[sampling]
normal_interval = 5       # seconds
elevated_interval = 1     # seconds
elevation_threshold = 30  # stress score
critical_threshold = 60   # stress score

[retention]
samples_days = 30
events_days = 90

[suspects]
patterns = ["codemeter", "bitdefender", "biomesyncd", "motu", "coreaudiod"]
```
