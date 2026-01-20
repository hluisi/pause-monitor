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

    -- Raw metrics
    cpu_pct     REAL,           -- System-wide CPU %
    load_avg    REAL,           -- 1-minute load average
    mem_available INTEGER,      -- Bytes available (not "free")
    swap_used   INTEGER,        -- Bytes in swap
    io_read     INTEGER,        -- Bytes/sec read
    io_write    INTEGER,        -- Bytes/sec write
    net_sent    INTEGER,        -- Bytes/sec sent
    net_recv    INTEGER,        -- Bytes/sec received
    cpu_temp    REAL,           -- Celsius (null if unprivileged)
    cpu_freq    INTEGER,        -- MHz (null if unprivileged)
    throttled   BOOLEAN,        -- Thermal throttling active (null if unprivileged)
    gpu_pct     REAL,           -- GPU utilization % (null if unprivileged)

    -- Stress breakdown (for historical analysis)
    stress_total   INTEGER,     -- Combined stress score 0-100
    stress_load    INTEGER,     -- Load contribution 0-40
    stress_memory  INTEGER,     -- Memory contribution 0-30
    stress_thermal INTEGER,     -- Thermal contribution 0-20
    stress_latency INTEGER,     -- Latency contribution 0-30
    stress_io      INTEGER      -- I/O contribution 0-20
)

-- Per-process snapshots (linked to samples, only for top consumers)
-- Top 10 by CPU + top 5 by I/O (if privileged) + any suspects
process_samples (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER REFERENCES samples(id),
    pid         INTEGER,
    name        TEXT,           -- Process name
    cpu_pct     REAL,
    mem_pct     REAL,
    io_read     INTEGER,        -- Bytes/sec (via iotop, null if unprivileged)
    io_write    INTEGER,        -- Bytes/sec (via iotop, null if unprivileged)
    is_suspect  BOOLEAN         -- Matches suspect list
)

-- Pause events (when interval > threshold)
events (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL,
    duration    REAL,           -- Seconds system was unresponsive

    -- Stress state before pause (from last sample)
    stress_total   INTEGER,
    stress_load    INTEGER,
    stress_memory  INTEGER,
    stress_thermal INTEGER,
    stress_latency INTEGER,
    stress_io      INTEGER,

    -- Identified culprits (JSON array of {pid, name, reason})
    culprits    TEXT,           -- e.g., [{"pid": 123, "name": "mdworker", "reason": "I/O"}]

    -- Forensics paths
    event_dir   TEXT,           -- Path to event directory
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
| **Disk I/O saturation** | `iotop` aggregate | High disk activity blocks processes |
| **Memory pressure** | `memory_pressure` command | System is compressing/swapping |
| **Thermal throttling** | `powermetrics` (privileged) | CPU running below capacity |
| **Self-latency** | `actual_sleep - expected_sleep` | Direct measure of responsiveness |
| **Run queue depth** | `vm_stat` pageins/outs rate | Paging activity spike |

Note: `psutil.cpu_times().iowait` is Linux-only. On macOS, we infer I/O pressure from disk activity rates and self-latency.

### Stress Score Calculation

Returns both total score and per-factor breakdown for TUI display:

```python
@dataclass
class StressBreakdown:
    load: int       # 0-40: load/cores ratio
    memory: int     # 0-30: memory pressure
    thermal: int    # 0-20: throttling active
    latency: int    # 0-30: self-latency
    io: int         # 0-20: disk I/O spike

    @property
    def total(self) -> int:
        return min(100, self.load + self.memory + self.thermal + self.latency + self.io)

def calculate_stress() -> StressBreakdown:
    # Load average relative to cores (max 40 points)
    load_ratio = load_avg_1min / core_count
    load_score = min(40, max(0, (load_ratio - 1.0) * 20))

    # Memory pressure (max 30 points)
    # Note: Use "available" memory, not "free"—macOS caches aggressively
    mem_score = min(30, max(0, (20 - mem_available_pct) * 1.5))

    # Thermal throttling (20 points if active, privileged only)
    thermal_score = 20 if throttled else 0

    # Self-latency (max 30 points)
    latency_ratio = actual_interval / expected_interval
    latency_score = min(30, max(0, (latency_ratio - 1.0) * 20)) if latency_ratio > 1.5 else 0

    # Disk I/O spike (max 20 points, privileged only)
    io_score = 20 if io_total > io_baseline * 10 else 0

    return StressBreakdown(
        load=int(load_score),
        memory=int(mem_score),
        thermal=thermal_score,
        latency=int(latency_score),
        io=io_score
    )
```

### Culprit Identification

When stress is elevated, identify processes that correlate with active stress factors:

```python
def identify_culprits(breakdown: StressBreakdown, processes: list[Process]) -> list[Process]:
    culprits = []

    # If load is contributing, flag top CPU consumers
    if breakdown.load > 0:
        culprits.extend(p for p in processes if p.cpu_pct > 50)

    # If I/O is contributing, flag top I/O consumers (privileged)
    if breakdown.io > 0:
        culprits.extend(p for p in processes if p.io_total > 50_000_000)  # 50 MB/s

    # Always flag suspect pattern matches
    culprits.extend(p for p in processes if p.is_suspect)

    return dedupe_by_pid(culprits)
```

### Adaptive Sampling

- **Normal mode:** 5s intervals
- **Elevated mode (stress > 30):** 1s intervals
- **Critical (stress > 60):** Preemptive snapshot capture

### Elevation Triggers

- Load average exceeds core count
- Disk I/O spike (10x baseline)
- Memory available below 20%
- Thermal throttling active
- Self-latency spike (sleep took 50% longer than expected)
- Suspect process exceeds 30% CPU

## Forensics Capture

When a pause is detected (interval > 2x expected):

1. **Immediate process snapshot** (~50ms) - Full process list via psutil
2. **Disk I/O snapshot** (~1s, privileged) - `sudo iotop -C 1 1` for per-process I/O
3. **Trigger spindump** (~5-10s, privileged) - `sudo spindump -reveal -noProcessingWhileSampling`
4. **Thermal snapshot** (~1s, privileged) - `sudo powermetrics -n 1` for temps/throttling
5. **Extract system logs** (~1s) - Filter for errors, hangs, memory warnings

### Forensics Storage

```
~/.local/share/pause-monitor/events/<timestamp>/
├── processes.json      # Full process snapshot with CPU/mem
├── disk_io.json        # Per-process I/O rates (privileged)
├── thermals.json       # CPU temp, freq, throttle state (privileged)
├── spindump.txt        # Thread stacks, 2-5 MB (privileged)
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
│  CPU:  ████████░░░░░░░░ 47%    Load: 3.4/16    Freq: 3.2 GHz          │
│  Mem:  ████████████░░░░ 73%    Avail: 34 GB    Pressure: Low          │
│  I/O:  ██░░░░░░░░░░░░░░  8%    R: 12 MB/s     W: 45 MB/s              │
│  Temp: ████░░░░░░░░░░░░ 52°C   Throttle: No   GPU: 23%                │
│                                                                        │
│  TOP PROCESSES                                        CPU   MEM    I/O │
│  ───────────────────────────────────────────────────────────────────── │
│  claude -c                                           106%  4.5%  2 MB/s│
│  claude                                               87%  0.5%  0 MB/s│
│  mdworker_shared                                      12%  0.1% 89 MB/s│
│  ghostty                                              24% 57.4%  1 MB/s│
│  WindowServer                                         20%  0.2%  0 MB/s│
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

### Elevated State Display

When stress exceeds 30%, the dashboard highlights contributing factors and suspected processes:

```
┌─ pause-monitor ──────────────────────────────────────────── 14:22:07 ─┐
│                                                                        │
│  ⚠ ELEVATED             STRESS: ████████░░ 78%      Mode: Elevated 1s │
│  ───────────────────────────────────────────────────────────────────── │
│  STRESS BREAKDOWN                                                      │
│    Load:     ████████████████ +32  (load 4.2 on 2 cores)              │
│    I/O:      ████████████░░░░ +20  (spike: 340 MB/s write)            │
│    Memory:   ██████░░░░░░░░░░ +12  (14% available)                    │
│    Thermal:  ░░░░░░░░░░░░░░░░  +0  (not throttled)                    │
│    Latency:  ██████████░░░░░░ +14  (1.7x expected)                    │
│                                                                        │
│  SUSPECTED CULPRITS                                   CPU   MEM    I/O │
│  ───────────────────────────────────────────────────────────────────── │
│  ★ mdworker_shared                                    45%  0.2% 298 MB/s│
│  ★ mds_stores                                         89%  1.2%  42 MB/s│
│    kernel_task                                       112%  0.0%   0 MB/s│
│    claude -c                                         106%  4.5%   2 MB/s│
│                                                                        │
│  ● 14:21:58  Elevated: I/O spike (mdworker_shared)                    │
│  ● 14:21:45  Elevated: Load exceeded cores                            │
│                                                                        │
│  [q] Quit  [e] Events  [p] Processes  [h] History  [?] Help           │
└────────────────────────────────────────────────────────────────────────┘
```

**Key features:**
- **Stress breakdown** shows contribution from each factor with visual bars
- **Suspected culprits** (★) are processes that correlate with stress factors:
  - High I/O process when I/O is spiking
  - High CPU process when load exceeds cores
  - Processes matching suspect patterns
- **Timeline** shows what triggered elevation and when

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

## Privileged Operations

Several valuable metrics require root access on macOS. Rather than running the entire daemon as root, we use a **sudoers.d approach** that grants passwordless access to specific, constrained commands.

### Why Not Run as Root?

- Larger attack surface
- Unnecessary for most operations (psutil works fine unprivileged)
- Violates principle of least privilege

### Installation

During `pause-monitor install --privileged`, we create a sudoers rule:

```bash
# /etc/sudoers.d/pause-monitor
%admin ALL=(root) NOPASSWD: /usr/sbin/spindump -reveal -noProcessingWhileSampling -o *
%admin ALL=(root) NOPASSWD: /usr/bin/powermetrics -n 1 -i 1 --samplers smc,cpu_power,gpu_power -o *
%admin ALL=(root) NOPASSWD: /usr/bin/iotop -C 1 1
```

The daemon then calls these via `sudo` without password prompts.

### Privileged Metrics

| Metric | Source | Why It Matters |
|--------|--------|----------------|
| **Per-process disk I/O** | `iotop -C 1 1` | Identifies which process is hammering the disk—often the pause culprit |
| **CPU temperature** | `powermetrics --samplers smc` | High temps trigger throttling |
| **Thermal throttling** | `powermetrics --samplers cpu_power` | Direct indicator of degraded performance |
| **CPU frequency** | `powermetrics --samplers cpu_power` | Shows if running at boost or base clock |
| **GPU utilization** | `powermetrics --samplers gpu_power` | GPU-bound workloads can stall the system |
| **Thread stacks** | `spindump` | Post-pause forensics showing what threads were doing |

### Graceful Degradation

If privileged access isn't configured, the daemon still works:
- Per-process I/O columns show NULL
- Temperature/throttling unavailable
- Spindump skipped during forensics
- Warning logged on startup: "Privileged metrics unavailable. Run `pause-monitor install --privileged` for full functionality."

### Security Considerations

- Commands are **exact patterns**—no shell injection possible
- Output paths constrained to user directories
- Only `%admin` group (macOS default for admin users) gets access
- Easily removed: `sudo rm /etc/sudoers.d/pause-monitor`
