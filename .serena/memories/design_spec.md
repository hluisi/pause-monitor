# Design Specification

> ⚠️ **COLLECTOR REDESIGN PENDING (2026-01-29).** TopCollector to be replaced with LibprocCollector.

**Last updated:** 2026-01-29
**Primary language:** Python

## Source Documents

| Document | Date Processed | Status |
|----------|----------------|--------|
| docs/plans/2026-01-20-pause-monitor-design.md | 2026-01-21 | Archived |
| docs/plans/2026-01-21-ring-buffer-sentinel-design.md | 2026-01-22 | Archived |
| docs/plans/2026-01-21-pause-monitor-redesign.md | 2026-01-23 | Archived |
| docs/plans/2026-01-21-pause-monitor-implementation.md | 2026-01-23 | Archived |
| docs/plans/phase-4-socket-server.md | 2026-01-23 | Archived |
| docs/plans/phase-5-socket-client-tui.md | 2026-01-23 | Archived |
| docs/plans/phase-6-cleanup.md | 2026-01-23 | Archived |
| docs/plans/2026-01-23-per-process-stressor-scoring-design.md | 2026-01-24 | **SUPERSEDED** |
| docs/plans/2026-01-23-per-process-stressor-scoring-plan.md | 2026-01-24 | **SUPERSEDED** |
| docs/plans/2026-01-23-tui-redesign-design.md | 2026-01-24 | Active |
| docs/plans/2026-01-25-per-process-band-tracking-design.md | 2026-01-27 | Implemented |
| docs/plans/2026-01-27-per-process-band-tracking-plan.md | 2026-01-27 | Implemented |

## Overview

A **real-time** system health monitoring tool for macOS that tracks down intermittent system pauses. Uses per-process stress scoring to identify specific culprit processes. Primary interface is a live TUI dashboard.

**Goals:**
1. Root cause identification - Identify specific processes causing stress via per-process scoring
2. Historical trending - Track process behavior over days/weeks to spot patterns via ProcessTracker events
3. Real-time alerting - Know when the system is under stress before it freezes

## Architecture (Target)

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │    Daemon    │───▶│   Storage    │◀───│     CLI      │       │
│  │  (libproc)   │    │   (SQLite)   │    │   Queries    │       │
│  └──────┬───────┘    └──────────────┘    └──────────────┘       │
│         │                   ▲                                    │
│         │                   │ process_events                     │
│  ┌──────▼───────┐           │                                    │
│  │ProcessTracker│───────────┘                                    │
│  │ (band track) │                                                │
│  └──────────────┘                                                │
│         │                                                        │
│         │ socket                                                 │
│         ▼                                                        │
│  ┌──────────────┐    ┌──────────────┐                           │
│  │ SocketServer │───▶│     TUI      │                           │
│  │  (real-time) │    │  Dashboard   │                           │
│  └──────────────┘    └──────────────┘                           │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐                                               │
│  │   Forensics  │                                               │
│  │  (on pause)  │                                               │
│  └──────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### LibprocCollector (collector.py) — TO BE IMPLEMENTED

**Purpose:** Per-process data collection via native macOS APIs (libproc.dylib)

**Why replacing TopCollector:**
- TopCollector spawns `top -l 2` subprocess every 2 seconds
- Throws away 50% of samples (first sample has invalid CPU%)
- Text parsing is fragile and slow
- Missing metrics: disk I/O, energy, instructions, cycles, GPU

**New approach:**
- Direct syscalls via ctypes to `/usr/lib/libproc.dylib`
- `proc_pid_rusage()` for CPU, memory, disk I/O, energy, wakeups
- `proc_pidinfo()` for context switches, syscalls, threads
- `sysctl` for process state
- IOKit for GPU metrics (optional)
- No subprocess spawning, no text parsing

**Data available (see `libproc_and_iokit_research` memory):**
- All current metrics (CPU, mem, state, pageins, csw, syscalls, threads)
- NEW: Disk I/O (bytes read/written)
- NEW: Energy (billed/serviced)
- NEW: CPU instructions and cycles
- NEW: Wakeups (package + interrupt)
- NEW: QoS breakdown
- NEW: Peak memory
- NEW: Per-process GPU time (via IOKit)

### ~~TopCollector (collector.py)~~ — DEPRECATED

> ⚠️ **DO NOT USE.** Being replaced with LibprocCollector.

The old approach used `top -l 2 -s 1` which:
- Spawns a subprocess every 2 seconds
- Wastes the first sample (invalid CPU%)
- Parses text output
- Misses many available metrics

### ProcessTracker (tracker.py)
- **Purpose:** Per-process band tracking with event lifecycle management
- **Tracks:** Individual processes crossing configurable band thresholds
- **Events:** Creates events with entry/exit/checkpoint snapshots
- **Boot time:** Uses `get_boot_time()` to invalidate stale PIDs across reboots

### Daemon (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, tracking, and forensics
- **Loop:** Continuous loop driven by collector
- **Integration:** Feeds ProcessSamples to ProcessTracker for persistence
- **Forensics:** Triggers spindump/tailspin on high scores or timing anomalies

### RingBuffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for pre-pause context
- **Size:** 30 samples (30 seconds at 1Hz)
- **Contents:** ProcessSamples batches

### SocketServer / SocketClient
- **Purpose:** Real-time streaming to TUI via Unix socket
- **Protocol:** Newline-delimited JSON at `~/.local/share/pause-monitor/daemon.sock`

### Storage (storage.py)
- **Purpose:** SQLite with WAL mode, schema v9
- **Primary tables:** `process_events`, `process_snapshots`, `system_samples`
- **Support tables:** `daemon_state` (key-value store)

### TUI (tui/app.py)
- **Purpose:** Live dashboard with score gauge, rogue processes, tracked events
- **Framework:** Textual
- **Data:** Real-time via socket, events from SQLite

---

## Per-Process Band Tracking (IMPLEMENTED)

### Y-Statement Summary

**In the context of** tracking which processes cause system stress and when,
**facing** ephemeral ring buffer data that disappears after 30 seconds,
**we decided for** event-based tracking where crossing a threshold creates an event with captured snapshots,
**to achieve** forensic data for analysis and historical trends across boot sessions,
**accepting** the need to checkpoint during long BAD periods and invalidate stale PIDs on reboot.

### Two States

| State | Behavior |
|-------|----------|
| **NORMAL** | Score below tracking_threshold. No persistence. Ring buffer has it. |
| **BAD** | Score at or above tracking_threshold. Event created, snapshots captured. |

### Event Lifecycle

1. Score crosses tracking_threshold → Create event, capture entry snapshot
2. New peak score while BAD → Update peak_score, peak_snapshot, peak_band
3. Checkpoint interval while BAD → Add checkpoint snapshot
4. Score drops below threshold → Add exit snapshot, set exit_time
5. PID disappears → Close event (no exit snapshot available)

---

## Data Models

### ProcessScore (collector.py)
```python
@dataclass
class ProcessScore:
    pid: int
    command: str
    cpu: float
    state: str
    mem: int
    cmprs: int
    pageins: int
    csw: int
    sysbsd: int
    threads: int
    score: int
    categories: frozenset[str]
    captured_at: float
    # Future: disk_read, disk_write, energy, instructions, cycles, gpu_time
```

### ProcessSamples (collector.py)
```python
@dataclass
class ProcessSamples:
    timestamp: float
    elapsed_ms: int
    process_count: int
    max_score: int
    rogues: list[ProcessScore]
```

### Database Tables (storage.py, v9)

**process_events:**
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| pid | INTEGER | Process ID |
| command | TEXT | Process command name |
| boot_time | INTEGER | System boot time for PID disambiguation |
| entry_time | REAL | When process crossed threshold |
| exit_time | REAL | When process dropped below (NULL if active) |
| entry_band | TEXT | Band at entry (elevated, high, critical) |
| peak_band | TEXT | Highest band reached |
| peak_score | INTEGER | Highest score during event |
| peak_snapshot | TEXT | JSON ProcessScore at peak |

**process_snapshots:**
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| event_id | INTEGER FK | References process_events(id) |
| snapshot_type | TEXT | 'entry', 'checkpoint', 'exit' |
| snapshot | TEXT | JSON ProcessScore |

## Configuration

Location: `~/.config/pause-monitor/config.toml`

### BandsConfig (config.py)
```python
@dataclass
class BandsConfig:
    low: int = 20        # 0-19 = low
    medium: int = 40     # 20-39 = medium
    elevated: int = 60   # 40-59 = elevated
    high: int = 80       # 60-79 = high
    critical: int = 100  # 80-100 = critical
    tracking_band: str = "elevated"
    forensics_band: str = "high"
    checkpoint_interval: int = 30
    forensics_cooldown: int = 60
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pause-monitor daemon` | Run background sampler (foreground) |
| `pause-monitor tui` | Launch interactive dashboard |
| `pause-monitor status` | Quick health check |
| `pause-monitor events` | List process events from current boot |
| `pause-monitor events <id>` | Inspect specific event |
| `pause-monitor history` | Query historical event data |
| `pause-monitor config` | Manage configuration |
| `pause-monitor prune` | Manual data cleanup |
| `pause-monitor install` | Set up launchd service |
| `pause-monitor uninstall` | Remove launchd service |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-process scoring over system-wide stress | Identifies specific culprits, not just "system stressed" |
| **libproc over top** | Direct API = no subprocess, no parsing, more data |
| 8-factor weighted scoring with multi-category bonus | Flexible, configurable identification of stress types |
| ProcessTracker per-process events | Historical record of which processes caused stress |
| Boot time for PID disambiguation | PIDs can be reused across reboots |
| Binary NORMAL/BAD states | Actions are binary; five bands are just descriptive labels |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |
| Daemon log | `~/.local/share/pause-monitor/daemon.log` |
| Socket | `~/.local/share/pause-monitor/daemon.sock` |
