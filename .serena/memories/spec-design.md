---
id: spec-design
type: spec
domain: project
subject: design
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [design_spec]
tags: []
related: []
sources: []
---

# Design Specification

**Last updated:** 2026-02-02
**Schema version:** 18

## Overview

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes consuming disproportionate system resources. Uses per-process resource-share scoring to identify specific culprits. Primary interface is a live TUI dashboard.

**Goals:**
1. **Rogue identification** - Continuously identify processes using disproportionate resources
2. **Historical tracking** - Track rogue process behavior over days/weeks via ProcessTracker events
3. **Real-time visibility** - Always show top potential rogues, even on healthy systems
4. **Forensic capture** - Automatically capture diagnostic data when processes cross thresholds

## Disproportionate-Share Scoring (v18)

The scoring system calculates each process's **share** of system resources relative to fair share:

| Resource | What It Measures |
|----------|------------------|
| **CPU** | CPU time relative to fair share per active process |
| **GPU** | GPU time consumption |
| **Memory** | Resident memory footprint |
| **Disk** | I/O bytes read/written |
| **Wakeups** | Interrupt wakeups (power impact) |

**Fair Share Calculation:**
```
fair_share = 1.0 / active_processes
cpu_share = process_cpu / (active_processes × per_core_fair_share)
```

**Disproportionality** = max(cpu_share, gpu_share, mem_share, disk_share, wakeups_share)

A process using 15% of CPU when fair share is 1% has 15× disproportionality.

**Score Calculation (logarithmic):**
- 1.0× fair share → score 0
- 50× fair share → score ~56
- 100× fair share → score ~66
- 200× fair share → score ~76

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
- IOKit for GPU metrics
- No subprocess spawning, no text parsing
- Scores ALL processes, selects top N for display (always has data)

**Sample Rate:** 3Hz (0.333s interval)

### ProcessTracker (tracker.py)
- **Purpose:** Per-process tracking with event lifecycle management
- **Tracks:** Individual processes crossing configurable thresholds
- **Events:** Creates events with entry/exit/checkpoint snapshots
- **Boot time:** Uses `get_boot_time()` to invalidate stale PIDs across reboots

### Daemon (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, tracking, and forensics
- **Loop:** Continuous loop driven by collector at 3Hz
- **Forensics:** Triggers tailspin/logs when process enters critical band

### RingBuffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for pre-incident context
- **Size:** 60 samples (~20 seconds at 3Hz)
- **Contents:** ProcessSamples batches (top N rogues per sample)

### SocketServer / SocketClient
- **Purpose:** Real-time streaming to TUI via Unix socket
- **Protocol:** Newline-delimited JSON at `/tmp/rogue-hunter/daemon.sock`

### Storage (storage.py)
- **Purpose:** SQLite with WAL mode, schema v18
- **Primary tables:** `process_events`, `process_snapshots`
- **Support tables:** `daemon_state`, `forensic_captures`, `spindump_*`, `log_*`, `buffer_context`

### TUI (tui/app.py)
- **Purpose:** Single-screen btop-style dashboard showing top rogues
- **Framework:** Textual
- **Data:** Real-time via socket only (no DB queries)
- **Always shows data:** Top N processes by score, even on healthy systems

---

## Per-Process Tracking

### Y-Statement Summary

**In the context of** tracking which processes are behaving badly and when,
**facing** ephemeral ring buffer data that disappears after 20 seconds,
**we decided for** event-based tracking where crossing a threshold creates an event with captured snapshots,
**to achieve** forensic data for analysis and historical trends across boot sessions,
**accepting** the need to checkpoint during long rogue periods and invalidate stale PIDs on reboot.

### Two States

| State | Behavior |
|-------|----------|
| **NORMAL** | Score below tracking_threshold (30). No persistence. Ring buffer has it. |
| **ROGUE** | Score at or above tracking_threshold. Event created, snapshots captured. |

### Event Lifecycle

1. Score crosses tracking_threshold → Create event, capture entry snapshot
2. New peak score while ROGUE → Update peak_score, peak_snapshot, peak_band
3. Checkpoint interval while ROGUE → Add checkpoint snapshot
4. Score drops below threshold for N samples → Add exit snapshot, set exit_time
5. PID disappears → Close event (no exit snapshot available)

---

## Data Models

### ProcessScore (collector.py) — THE Canonical Schema

**Identity:** `pid`, `command`, `captured_at`

**Metrics (all with current/low/high via MetricValue):**
- CPU: `cpu`
- Memory: `mem`, `mem_peak`, `pageins`, `pageins_rate`, `faults`, `faults_rate`
- Disk I/O: `disk_io`, `disk_io_rate`
- Activity: `csw`, `csw_rate`, `syscalls`, `syscalls_rate`, `threads`, `mach_msgs`, `mach_msgs_rate`
- Efficiency: `instructions`, `cycles`, `ipc`
- Power: `energy`, `energy_rate`, `wakeups`, `wakeups_rate`
- Contention: `runnable_time`, `runnable_time_rate`, `qos_interactive`, `qos_interactive_rate`
- GPU: `gpu_time`, `gpu_time_rate`
- State: `state`, `priority`

**Scoring:**
- `score` (0-100)
- `band` (low/medium/elevated/high/critical)
- `cpu_share`, `gpu_share`, `mem_share`, `disk_share`, `wakeups_share`
- `disproportionality` (max of all shares)
- `dominant_resource` (which resource is highest)

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

## Configuration

Location: `~/.config/rogue-hunter/config.toml`

### SystemConfig
| Field | Default | Purpose |
|-------|---------|---------|
| `ring_buffer_size` | 60 | Samples in ring buffer |
| `sample_interval` | 0.333 | Seconds between samples (3Hz) |
| `forensics_debounce` | 2.0 | Min seconds between forensics |

### BandsConfig
| Band | Threshold | Behavior |
|------|-----------|----------|
| low | 0 | No persistence |
| medium | 30 | Tracking starts, checkpoints every 60 samples |
| elevated | 45 | Checkpoints every 30 samples |
| high | 60 | Every sample persisted |
| critical | 80 | Forensics triggered |

| Setting | Default |
|---------|---------|
| `tracking_band` | "medium" (threshold 30) |
| `forensics_band` | "critical" (threshold 80) |
| `medium_checkpoint_samples` | 60 (~20s) |
| `elevated_checkpoint_samples` | 30 (~10s) |
| `event_cooldown_seconds` | 60.0 |
| `exit_stability_samples` | 15 |

## CLI Commands

| Command | Purpose |
|---------|---------|
| `rogue-hunter daemon` | Run background sampler (foreground) |
| `rogue-hunter tui` | Launch interactive dashboard |
| `rogue-hunter status` | Quick health check |
| `rogue-hunter events` | List process events from current boot |
| `rogue-hunter events show <id>` | Inspect specific event |
| `rogue-hunter history` | Query historical event data |
| `rogue-hunter config` | Manage configuration |
| `rogue-hunter prune` | Manual data cleanup |
| `rogue-hunter perms install` | Set up sudoers for forensics |
| `rogue-hunter service install` | Set up launchd service |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Disproportionate-share scoring | Identifies which resource a process is hogging |
| **libproc (not top)** | Direct API = no subprocess, no parsing, more data |
| Always show top N | TUI always has data; threshold only affects persistence |
| ProcessTracker per-process events | Historical record of which processes went rogue |
| Boot time for PID disambiguation | PIDs can be reused across reboots |
| Binary NORMAL/ROGUE states | Actions are binary; bands are descriptive labels |
| Socket-only TUI | Real-time streaming, no DB queries, minimal latency |
| Forensics on critical only | Reduces noise; only capture truly problematic processes |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/rogue-hunter/config.toml` |
| Database | `~/.local/share/rogue-hunter/data.db` |
| Daemon log | `~/.local/state/rogue-hunter/daemon.log` |
| Socket | `/tmp/rogue-hunter/daemon.sock` |
