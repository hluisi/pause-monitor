# Design Specification

> ✅ **Phase 7 COMPLETE (2026-01-24).** Per-process scoring with TopCollector at 1Hz; SCHEMA_VERSION=7.
> 🆕 **Per-Process Band Tracking DESIGNED (2026-01-25).** Event-based tracking when processes cross threshold. See "Per-Process Band Tracking" section.

**Last updated:** 2026-01-25
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
| docs/plans/2026-01-23-per-process-stressor-scoring-design.md | 2026-01-24 | Implemented |
| docs/plans/2026-01-23-per-process-stressor-scoring-plan.md | 2026-01-24 | Implemented |
| docs/plans/2026-01-23-tui-redesign-design.md | 2026-01-24 | Active |
| docs/plans/2026-01-25-per-process-band-tracking-design.md | 2026-01-25 | **NEW - Pending Implementation** |

## Overview

A **real-time** system health monitoring tool for macOS that tracks down intermittent system pauses. Uses per-process stress scoring to identify specific culprit processes. Primary interface is a live TUI dashboard.

**Goals:**
1. Root cause identification - Identify specific processes causing stress via per-process scoring
2. Historical trending - Track process behavior over days/weeks to spot patterns
3. Real-time alerting - Know when the system is under stress before it freezes

## Architecture (Current - Phase 7)

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │    Daemon    │───▶│   Storage    │◀───│     CLI      │       │
│  │   (1Hz top)  │    │   (SQLite)   │    │   Queries    │       │
│  └──────┬───────┘    └──────────────┘    └──────────────┘       │
│         │                   ▲                                    │
│         │ socket            │ events only                        │
│         ▼                   │                                    │
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

## Components (Current)

### TopCollector (collector.py)
- **Purpose:** Per-process data collection via `top` command at 1Hz
- **Command:** `top -l 2 -s 1 -stats pid,command,cpu,state,mem,cmprs,threads,csw,sysbsd,pageins`
- **Output:** `ProcessSamples` with list of `ProcessScore` for rogue processes
- **Scoring:** 8-factor weighted scoring (cpu, state, pageins, mem, cmprs, csw, sysbsd, threads)

### Daemon (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, detection, and forensics
- **Loop:** Single 1Hz loop driven by TopCollector
- **Tier decisions:** Based on `ProcessSamples.max_score` (highest scoring rogue)
- **Storage:** Events and ProcessSamples stored on tier 2+ escalation

### TierManager (sentinel.py)
- **Purpose:** Tier state machine for stress level transitions
- **Tiers:** SENTINEL (1), ELEVATED (2), CRITICAL (3)
- **Thresholds:** elevated=50, critical=75 (configurable)
- **Hysteresis:** 5s delay for de-escalation

### RingBuffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for pre-pause context
- **Size:** 30 samples (30 seconds at 1Hz)
- **Contents:** ProcessSamples with tier metadata

### SocketServer / SocketClient
- **Purpose:** Real-time streaming to TUI via Unix socket
- **Protocol:** Newline-delimited JSON at `~/.local/share/pause-monitor/daemon.sock`

### Storage (storage.py)
- **Purpose:** SQLite with WAL mode, schema v7
- **Primary tables:** `events`, `process_sample_records` (JSON blob storage)
- **Legacy tables:** `event_samples`, `samples`, `process_samples` (unused)

### TUI (tui/app.py)
- **Purpose:** Live dashboard with stress gauge, rogue processes, events
- **Framework:** Textual
- **Data:** Real-time via socket, events from SQLite

---

## Per-Process Band Tracking (PLANNED)

> **Status:** Design complete (2026-01-25). Implementation pending.
> **Document:** `docs/plans/2026-01-25-per-process-band-tracking-design.md`

### Y-Statement Summary

**In the context of** tracking which processes cause system stress and when,
**facing** ephemeral ring buffer data that disappears after 30 seconds and no historical record of process behavior,
**we decided for** event-based tracking where crossing a threshold creates an event with captured snapshots,
**to achieve** forensic data for analysis and historical trends across boot sessions,
**accepting** the need to checkpoint during long BAD periods and migrate data on reboot.

### Problem Statement

Current system captures real-time ProcessScore data at 1Hz in a 30-second ring buffer. Data older than 30 seconds is lost. No way to answer:
- "What happened when chrome was stressed for 5 minutes?"
- "Which processes have been problematic this week?"
- "What did the system look like when this process peaked?"

### Two States (Not Five)

The five bands (low, medium, elevated, high, critical) describe **how bad** a score is. But the action is binary:

| State | Behavior |
|-------|----------|
| **NORMAL** | Score below threshold. No persistence. Ring buffer has it if needed. |
| **BAD** | Score at or above threshold. Create event, capture snapshots, persist. |

### Event-Based Tracking

When a process crosses the threshold:
1. Create EVENT (unique ID ties all snapshots together)
2. Capture entry snapshot
3. Track peak (replace snapshot when new peak reached)
4. Checkpoint every ring buffer cycle
5. Capture exit snapshot when leaving BAD state
6. Event is complete

### Proposed Schema (v8)

```sql
CREATE TABLE process_events (
    id INTEGER PRIMARY KEY,
    pid INTEGER NOT NULL,
    command TEXT NOT NULL,
    boot_time INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,                    -- NULL if still active
    peak_score INTEGER NOT NULL,
    peak_snapshot TEXT NOT NULL,      -- JSON: ProcessScore at peak
    peak_captured_at REAL NOT NULL
);

CREATE TABLE process_snapshots (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    captured_at REAL NOT NULL,
    capture_type TEXT NOT NULL,       -- 'entry', 'checkpoint', 'exit'
    snapshot TEXT NOT NULL,           -- JSON: ProcessScore
    FOREIGN KEY (event_id) REFERENCES process_events(id)
);
```

### Configuration (Planned)

```toml
[bands]
low = 20        # 0-19 = low
medium = 40     # 20-39 = medium
elevated = 60   # 40-59 = elevated
high = 80       # 60-79 = high
critical = 100  # 80-100 = critical
threshold = 40  # BAD state trigger
```

### Triggers

| Trigger | Action |
|---------|--------|
| Score crosses threshold (enters BAD) | Create event, capture entry snapshot |
| New peak score while BAD | Update peak_score, peak_snapshot |
| Ring buffer cycle while BAD | Add checkpoint snapshot |
| Score drops below threshold (exits BAD) | Add exit snapshot, set ended_at |
| Daemon startup | Check boot_time, handle reboot |

---

## Data Models (Current - Phase 7)

### ProcessScore (collector.py)
```python
@dataclass
class ProcessScore:
    pid: int
    command: str
    cpu: float           # CPU% (0-100+)
    state: str           # running, sleeping, stuck, zombie, etc.
    mem: int             # Memory bytes
    cmprs: int           # Compressed memory bytes
    pageins: int         # Page-ins
    csw: int             # Context switches
    sysbsd: int          # BSD syscalls
    threads: int         # Thread count
    score: int           # Weighted score (0-100)
    categories: frozenset[str]  # Why included
```

### ProcessSamples (collector.py)
```python
@dataclass
class ProcessSamples:
    timestamp: float
    elapsed_ms: int
    process_count: int
    max_score: int           # Highest rogue score
    rogues: list[ProcessScore]
```

### Events table (storage.py)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| start_timestamp | REAL | Unix timestamp when escalation began |
| end_timestamp | REAL | Unix timestamp when returned to tier 1 |
| peak_stress | INTEGER | Highest max_score during event |
| peak_tier | INTEGER | Highest tier reached (2 or 3) |
| status | TEXT | "unreviewed", "reviewed", "pinned", "dismissed" |
| notes | TEXT | User-added notes |

### process_sample_records table (storage.py, v7)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| event_id | INTEGER FK | References events(id) |
| tier | INTEGER | 2=peak save, 3=continuous save |
| data | TEXT | JSON blob containing ProcessSamples |

## Configuration

Location: `~/.config/pause-monitor/config.toml`

### Current Config Sections

| Section | Key Options |
|---------|-------------|
| `[sampling]` | normal_interval, elevated_interval (legacy, unused with TopCollector) |
| `[retention]` | samples_days=30, events_days=90 |
| `[alerts]` | pause_detected, critical_stress, elevated_entered, sound |
| `[suspects]` | patterns (list of known problematic processes) |
| `[sentinel]` | fast_interval_ms, ring_buffer_seconds, pause_threshold_ratio |
| `[tiers]` | elevated_threshold=50, critical_threshold=75 |
| `[scoring]` | weights (cpu=25, state=20, pageins=15, mem=15, cmprs=10, csw=10, sysbsd=5) |
| `[scoring.state_multipliers]` | Post-score multipliers by state (idle=0.5 to stuck=1.0) |
| `[scoring.normalization]` | Normalization maxima for metrics |
| `[rogue_selection]` | Per-category selection config (cpu, mem, cmprs, threads, csw, sysbsd, pageins, state) |

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pause-monitor daemon` | Run background sampler (foreground) |
| `pause-monitor tui` | Launch interactive dashboard |
| `pause-monitor status` | Quick health check |
| `pause-monitor events` | List events (--status filter) |
| `pause-monitor events <id>` | Inspect specific event |
| `pause-monitor events mark <id> <status>` | Change event status |
| `pause-monitor history` | Query historical data |
| `pause-monitor config` | Manage configuration |
| `pause-monitor prune` | Manual data cleanup |
| `pause-monitor install` | Set up launchd service |
| `pause-monitor uninstall` | Remove launchd service |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-process scoring over system-wide stress | Identifies specific culprits, not just "system stressed" |
| TopCollector at 1Hz | `top -l 2` provides accurate CPU% deltas |
| 8-factor weighted scoring | Flexible, configurable identification of stress types |
| max_score for tier decisions | One rogue CAN pause a system — escalate on worst process |
| JSON blob storage (v7) | Flexible schema evolution, stores full ProcessSamples |
| Event-based band tracking (planned) | Capture forensic history without storage bloat |
| Binary NORMAL/BAD states (planned) | Actions are binary; five bands are just descriptive labels |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |
| Daemon log | `~/.local/share/pause-monitor/daemon.log` |
| Socket | `~/.local/share/pause-monitor/daemon.sock` |
