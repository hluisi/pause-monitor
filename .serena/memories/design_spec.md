# Design Specification

**Last updated:** 2026-01-31
**Primary language:** Python

## Source Documents

| Document | Date Processed | Status |
|----------|----------------|--------|
| docs/plans/2026-01-20-rogue-hunter-design.md | 2026-01-21 | Archived |
| docs/plans/2026-01-21-ring-buffer-sentinel-design.md | 2026-01-22 | Archived |
| docs/plans/2026-01-21-rogue-hunter-redesign.md | 2026-01-23 | Archived |
| docs/plans/2026-01-21-rogue-hunter-implementation.md | 2026-01-23 | Archived |
| docs/plans/phase-4-socket-server.md | 2026-01-23 | Archived |
| docs/plans/phase-5-socket-client-tui.md | 2026-01-23 | Archived |
| docs/plans/phase-6-cleanup.md | 2026-01-23 | Archived |
| docs/plans/2026-01-23-per-process-stressor-scoring-design.md | 2026-01-24 | **SUPERSEDED** |
| docs/plans/2026-01-23-per-process-stressor-scoring-plan.md | 2026-01-24 | **SUPERSEDED** |
| docs/plans/2026-01-23-tui-redesign-design.md | 2026-01-24 | Implemented |
| docs/plans/2026-01-25-per-process-band-tracking-design.md | 2026-01-27 | Implemented |
| docs/plans/2026-01-27-per-process-band-tracking-plan.md | 2026-01-27 | Implemented |

## Overview

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes negatively affecting system performance. Uses per-process scoring across four dimensions of rogue behavior to identify specific culprits. Primary interface is a live TUI dashboard.

**Goals:**
1. **Rogue identification** - Continuously identify processes behaving badly via 4-category scoring
2. **Historical tracking** - Track rogue process behavior over days/weeks via ProcessTracker events
3. **Real-time visibility** - Always show top potential rogues, even on healthy systems
4. **Forensic capture** - Automatically capture diagnostic data when processes cross thresholds

## The Four Dimensions of Rogue Behavior

| Category | Weight | What It Detects | Why It's Rogue |
|----------|--------|-----------------|----------------|
| **Blocking** | 40% | I/O bottlenecks, memory thrashing, disk saturation | Directly prevents other processes from running |
| **Contention** | 30% | CPU fighting, scheduler pressure, excessive context switching | Forces others to wait |
| **Pressure** | 20% | Memory hogging, kernel overhead, excessive wakeups | Degrades system capacity |
| **Efficiency** | 10% | Stalled pipelines, thread proliferation | Wastes resources others could use |

Blocking + Contention (70%) = "Hurting others" — should score highest
Pressure + Efficiency (30%) = "Resource hog" — secondary concern

## Architecture

```
                              Rogue Hunter Architecture
================================================================================

                    ┌─────────────────────────────────────┐
                    │              CLI (cli.py)           │
                    │  daemon │ tui │ status │ events    │
                    └─────────┬───────────────┬──────────┘
                              │               │
                              ▼               ▼
┌─────────────────────────────────────────┐   ┌──────────────────────────────────┐
│           DAEMON (daemon.py)            │   │          TUI (tui/app.py)        │
│                                         │   │                                  │
│  ┌─────────────┐   ┌─────────────┐      │   │  ┌──────────┐  ┌─────────────┐  │
│  │LibprocColl- │   │ProcessTrack-│      │   │  │HeaderBar │  │ProcessTable │  │
│  │ector        │   │er           │      │   │  │(sparkline)│  │(top rogues) │  │
│  │(collector.py│   │(tracker.py) │      │   │  └──────────┘  └─────────────┘  │
│  │)            │   │             │      │   │  ┌──────────┐  ┌─────────────┐  │
│  └──────┬──────┘   └──────┬──────┘      │   │  │Activity  │  │TrackedEvents│  │
│         │                 │             │   │  │Log       │  │Panel        │  │
│         ▼                 ▼             │   │  └──────────┘  └─────────────┘  │
│  ┌─────────────────────────────┐        │   │                                  │
│  │      RingBuffer             │        │   └──────────────────────────────────┘
│  │      (ringbuffer.py)        │        │                 ▲
│  └─────────────┬───────────────┘        │                 │
│                │                        │                 │ JSON over Unix socket
│                ▼                        │                 │
│  ┌─────────────────────────────┐        │   ┌────────────┴────────────┐
│  │    SocketServer             │◄───────┼───│     SocketClient        │
│  │    (socket_server.py)       │        │   │     (socket_client.py)  │
│  └─────────────────────────────┘        │   └─────────────────────────┘
│                                         │
│  ┌─────────────────────────────┐        │
│  │   ForensicsCapture          │        │
│  │   (forensics.py)            │        │
│  │   • tailspin (sudo)         │        │
│  │   • log show                │        │
│  └─────────────────────────────┘        │
│                │                        │
└────────────────┼────────────────────────┘
                 ▼
        ┌────────────────────────────────────────────┐
        │              SQLite (storage.py)           │
        │                                            │
        │  daemon_state │ process_events │ snapshots │
        │  forensic_captures │ spindump_* │ log_*   │
        └────────────────────────────────────────────┘
```

## Components

### LibprocCollector (collector.py)

**Purpose:** Per-process data collection via native macOS APIs (libproc.dylib)

**Approach:**
- Direct syscalls via ctypes to `/usr/lib/libproc.dylib`
- `proc_pid_rusage()` for CPU, memory, disk I/O, energy, wakeups
- `proc_pidinfo()` for context switches, syscalls, threads
- `sysctl` for process state
- No subprocess spawning, no text parsing
- Scores ALL processes, selects top N for display (always has data)

**Data collected (see `libproc_and_iokit_research` memory):**
- CPU % (calculated from time deltas)
- Memory (physical footprint, peak)
- Disk I/O (bytes read/written, rate)
- Energy (billed, rate)
- CPU instructions and cycles (IPC)
- Wakeups (package + interrupt)
- Context switches, syscalls, threads, mach messages
- Process state and priority
- Page-ins and faults

### ProcessTracker (tracker.py)
- **Purpose:** Per-process tracking with event lifecycle management
- **Tracks:** Individual processes crossing configurable thresholds
- **Events:** Creates events with entry/exit/checkpoint snapshots
- **Boot time:** Uses `get_boot_time()` to invalidate stale PIDs across reboots
- **Independence:** Applies its own threshold for persistence (separate from display selection)

### Daemon (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, tracking, and forensics
- **Loop:** Continuous loop driven by collector at 5Hz (0.2s interval)
- **Integration:** Feeds ProcessSamples to ProcessTracker for persistence
- **Forensics:** Triggers tailspin/logs when process enters high band

### RingBuffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for pre-incident context
- **Size:** 150 samples (30 seconds at 5Hz)
- **Contents:** ProcessSamples batches (top N rogues per sample)
- **Forensics:** Frozen and stored when incidents occur

### SocketServer / SocketClient
- **Purpose:** Real-time streaming to TUI via Unix socket
- **Protocol:** Newline-delimited JSON at `/tmp/rogue-hunter/daemon.sock`

### Storage (storage.py)
- **Purpose:** SQLite with WAL mode, schema v13
- **Primary tables:** `process_events`, `process_snapshots`
- **Support tables:** `daemon_state`, `forensic_captures`, `spindump_*`, `log_*`, `buffer_context`

### TUI (tui/app.py)
- **Purpose:** Single-screen btop-style dashboard showing top rogues
- **Framework:** Textual
- **Data:** Real-time via socket only (no DB queries)
- **Widgets:** HeaderBar (sparkline), ProcessTable, ActivityLog, TrackedEventsPanel
- **Always shows data:** Top N processes by score, even on healthy systems

---

## Per-Process Tracking (IMPLEMENTED)

### Y-Statement Summary

**In the context of** tracking which processes are behaving badly and when,
**facing** ephemeral ring buffer data that disappears after 30 seconds,
**we decided for** event-based tracking where crossing a threshold creates an event with captured snapshots,
**to achieve** forensic data for analysis and historical trends across boot sessions,
**accepting** the need to checkpoint during long rogue periods and invalidate stale PIDs on reboot.

### Two States

| State | Behavior |
|-------|----------|
| **NORMAL** | Score below tracking_threshold. No persistence. Ring buffer has it. |
| **ROGUE** | Score at or above tracking_threshold. Event created, snapshots captured. |

### Event Lifecycle

1. Score crosses tracking_threshold → Create event, capture entry snapshot
2. New peak score while ROGUE → Update peak_score, peak_snapshot, peak_band
3. Checkpoint interval while ROGUE → Add checkpoint snapshot
4. Score drops below threshold → Add exit snapshot, set exit_time
5. PID disappears → Close event (no exit snapshot available)

---

## Data Models

### MetricValue (collector.py)

```python
@dataclass
class MetricValue:
    current: float | int  # Current sample value
    low: float | int      # Minimum in ring buffer window
    high: float | int     # Maximum in ring buffer window
```

### ProcessScore (collector.py)

**Identity:** `pid`, `command`, `captured_at`

**All metrics use MetricValue (current/low/high):**
- CPU: `cpu`
- Memory: `mem`, `mem_peak` (int), `pageins`, `pageins_rate`, `faults`, `faults_rate`
- Disk I/O: `disk_io`, `disk_io_rate`
- Activity: `csw`, `csw_rate`, `syscalls`, `syscalls_rate`, `threads`, `mach_msgs`, `mach_msgs_rate`
- Efficiency: `instructions`, `cycles`, `ipc`
- Power: `energy`, `energy_rate`, `wakeups`, `wakeups_rate`
- Contention: `runnable_time`, `runnable_time_rate`, `qos_interactive`, `qos_interactive_rate`
- State: `state` (MetricValueStr), `priority`
- Scoring: `score`, `band` (MetricValueStr), `blocking_score`, `contention_score`, `pressure_score`, `efficiency_score`, `dominant_category`, `dominant_metrics`

### ProcessSamples (collector.py)
```python
@dataclass
class ProcessSamples:
    timestamp: datetime
    elapsed_ms: int
    process_count: int
    max_score: int        # Peak score from ALL processes
    rogues: list[ProcessScore]  # Top N by score (always populated)
```

### Database Tables (storage.py, v13)

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
| peak_snapshot_id | INTEGER | FK to process_snapshots |

**process_snapshots:**
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| event_id | INTEGER FK | References process_events(id) |
| snapshot_type | TEXT | 'entry', 'checkpoint', 'exit' |
| captured_at | REAL | Timestamp |
| (metric columns) | Various | Each MetricValue has current/low/high columns |

## Configuration

Location: `~/.config/rogue-hunter/config.toml`

### BandsConfig (config.py)
```python
@dataclass
class BandsConfig:
    medium: int = 40      # 0-39 = low
    elevated: int = 60    # 40-59 = medium
    high: int = 80        # 60-79 = elevated
    critical: int = 100   # 80-99 = high, 100 = critical
    tracking_band: str = "elevated"
    forensics_band: str = "high"
    checkpoint_interval: int = 30
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `rogue-hunter daemon` | Run background sampler (foreground) |
| `rogue-hunter tui` | Launch interactive dashboard |
| `rogue-hunter status` | Quick health check |
| `rogue-hunter events` | List process events from current boot |
| `rogue-hunter events <id>` | Inspect specific event |
| `rogue-hunter history` | Query historical event data |
| `rogue-hunter config` | Manage configuration |
| `rogue-hunter prune` | Manual data cleanup |
| `rogue-hunter install` | Set up launchd service |
| `rogue-hunter uninstall` | Remove launchd service |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-process scoring over system-wide stress | Identifies specific culprits, not just "system stressed" |
| **libproc (not top)** | Direct API = no subprocess, no parsing, more data |
| 4-category weighted scoring | Blocking/Contention/Pressure/Efficiency captures different rogue behaviors |
| Always show top N | TUI always has data; threshold only affects persistence |
| ProcessTracker per-process events | Historical record of which processes went rogue |
| Boot time for PID disambiguation | PIDs can be reused across reboots |
| Binary NORMAL/ROGUE states | Actions are binary; bands are descriptive labels |
| Socket-only TUI | Real-time streaming, no DB queries, minimal latency |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/rogue-hunter/config.toml` |
| Database | `~/.local/share/rogue-hunter/data.db` |
| Events | `~/.local/share/rogue-hunter/events/` |
| Daemon log | `~/.local/state/rogue-hunter/daemon.log` |
| Socket | `/tmp/rogue-hunter/daemon.sock` |
