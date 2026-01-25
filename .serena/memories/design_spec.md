# Design Specification

> ✅ **Phase 6 COMPLETE (2026-01-22).** Tier-based event storage redesign; SCHEMA_VERSION=6.
> ⚠️ **Per-Process Scoring Redesign Planned.** See "Per-Process Stressor Scoring" section below.

**Last updated:** 2026-01-24 (Added per-process scoring design and TUI redesign)
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
| docs/plans/2026-01-23-per-process-stressor-scoring-design.md | 2026-01-24 | Active |
| docs/plans/2026-01-23-per-process-stressor-scoring-plan.md | 2026-01-24 | Active |
| docs/plans/2026-01-23-tui-redesign-design.md | 2026-01-24 | Active |
| docs/plans/2026-01-23-per-process-stressor-scoring-plan-1st-try.md | 2026-01-24 | Superseded |

## Overview

A **real-time** system health monitoring tool for macOS that tracks down intermittent system pauses. Uses multi-factor stress detection to distinguish "busy but fine" from "system degraded." Primary interface is a live TUI dashboard.

**Goals:**
1. Root cause identification - Capture enough data during/after pauses to identify the culprit process
2. Historical trending - Track system behavior over days/weeks to spot patterns
3. Real-time alerting - Know when the system is under stress before it freezes

## Architecture (Current - Phase 6)

```
┌─────────────────────────────────────────────────────────────────┐
│                        pause-monitor                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Sampler    │───▶│   Storage    │◀───│     CLI      │       │
│  │   (daemon)   │    │   (SQLite)   │    │   Queries    │       │
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

### Sampler (daemon.py)
- **Purpose:** Background daemon orchestrating sampling, detection, and forensics
- **Responsibilities:** Stream powermetrics at 100ms, calculate stress, manage ring buffer, trigger forensics
- **Interfaces:** Reads config, writes to SQLite, triggers forensics, broadcasts via SocketServer
- **Key features:** 
  - Single 10Hz loop driven by powermetrics stream
  - TierManager for SENTINEL→ELEVATED→CRITICAL transitions
  - Ring buffer captures 30s of context before pauses
  - SocketServer broadcasts samples to TUI in real-time
  - Automatic culprit identification from ring buffer

### Tier Manager (sentinel.py)
- **Purpose:** Tier state machine for stress level transitions
- **Responsibilities:** Manage SENTINEL/ELEVATED/CRITICAL tier transitions with hysteresis
- **Components:**
  - `TierManager` - State machine with `update(stress_total)` returning `TierAction` on transitions
  - `Tier` enum - SENTINEL (1), ELEVATED (2), CRITICAL (3)
  - `TierAction` enum - TIER2_ENTRY, TIER2_EXIT, TIER2_PEAK, TIER3_ENTRY, TIER3_EXIT
- **Configuration:** `[tiers]` config section (elevated_threshold, critical_threshold)
- **Note:** The `Sentinel` class was deleted in Phase 5 redesign; daemon calls TierManager directly

### Ring Buffer (ringbuffer.py)
- **Purpose:** In-memory circular buffer for capturing pre-pause context
- **Responsibilities:** Store last N seconds of stress samples, capture process snapshots on tier transitions
- **Features:**
  - Thread-safe circular buffer
  - `freeze()` creates immutable snapshot for analysis
  - Process snapshots capture top 10 by CPU and memory

### Socket Server (socket_server.py)
- **Purpose:** Unix socket server for broadcasting real-time samples to TUI
- **Responsibilities:** Accept client connections, broadcast sample messages, manage client lifecycle
- **Protocol:** JSON messages over Unix domain socket at `~/.local/share/pause-monitor/daemon.sock`

### Socket Client (socket_client.py)
- **Purpose:** Unix socket client for TUI to receive real-time samples
- **Responsibilities:** Connect to daemon socket, receive and parse JSON messages
- **Usage:** TUI creates SocketClient, iterates async over messages to update widgets

### Storage (storage.py)
- **Purpose:** SQLite database with WAL mode for concurrent access
- **Responsibilities:** Store samples, process snapshots, events, daemon state
- **Interfaces:** Used by daemon (write), TUI (read-only), CLI (read)
- **Location:** `~/.local/share/pause-monitor/data.db`

### Collector (collector.py)
- **Purpose:** Stream and parse powermetrics plist output
- **Responsibilities:** Run powermetrics subprocess, parse NUL-separated plists, extract metrics
- **Key features:** Handles malformed plists, text headers, streaming output

### Forensics (forensics.py)
- **Purpose:** Capture diagnostic data on pause detection
- **Responsibilities:** Process snapshot, spindump, tailspin save, log extraction
- **Triggers:** When interval > 2x expected (pause detected)
- **Output:** Event directories in `~/.local/share/pause-monitor/events/`

### Stress Calculator (stress.py)
- **Purpose:** Multi-factor stress scoring
- **Responsibilities:** Calculate stress breakdown from metrics, identify culprits
- **Output:** StressBreakdown dataclass with 8 factors: load, memory, thermal, latency, io, gpu, wakeups, pageins (pageins is critical for pause detection)

### TUI Dashboard (tui/)
- **Purpose:** Live dashboard showing current stats, recent events, trends
- **Framework:** Textual
- **Views:** Dashboard (default), Processes, Events, History
- **Data source:** Real-time samples via SocketClient; events from SQLite

### CLI (cli.py)
- **Purpose:** Command-line interface
- **Framework:** Click

### Notifications (notifications.py)
- **Purpose:** macOS notification center alerts
- **Methods:** terminal-notifier (preferred) or osascript fallback

### Sleep/Wake Detection (sleepwake.py)
- **Purpose:** Distinguish sleep from actual pauses
- **Methods:** pmset log parsing, clock drift detection

---

## Per-Process Stressor Scoring (PLANNED REDESIGN)

> **Status:** Design complete, implementation plan ready. See `2026-01-23-per-process-stressor-scoring-design.md` and `2026-01-23-per-process-stressor-scoring-plan.md`.

### Y-Statement Summary

**In the context of** identifying what causes system pauses and slowdowns,
**facing** incomplete per-process data from powermetrics and a reactive "detect then hunt" model,
**we decided for** per-process stressor scoring using top at 1Hz with 8 weighted metrics,
**to achieve** proactive rogue process identification with built-in attribution,
**accepting** lower sample rate (1Hz vs 10Hz) and a breaking schema change (v7).

### Problem Statement

The current system calculates a single system-wide stress score then hunts for culprits when problems occur. This is backwards — we detect stress, then scramble to figure out who caused it.

The redesign flips this model: continuously identify which processes are causing the most trouble, rank them by a stressor score, and have attribution ready *before* problems manifest.

### New Data Source

Replace `powermetrics` with `top` (macOS) in delta mode at 1Hz.

| | powermetrics | top |
|--|--------------|-----|
| **Rate** | 10Hz | 1Hz |
| **Per-process metrics** | Limited (cpu, pageins only) | Complete (8 metrics) |

### Scoring Weights (8 factors, sum to 100)

| Metric | Weight | What it reveals |
|--------|--------|-----------------|
| `cpu` | 25 | CPU hogging — most common trouble sign |
| `state` | 20 | Stuck/frozen — binary and critical when present |
| `pageins` | 15 | Disk I/O for memory — catastrophic for performance |
| `mem` | 15 | Memory footprint — drives system-wide pressure |
| `cmprs` | 10 | Compressed memory — system struggling with this process |
| `csw` | 10 | Context switches — scheduling behavior, thrashing |
| `sysbsd` | 5 | BSD syscalls — kernel interaction overhead |
| `threads` | 0 | Used for selection only, not scoring |

### Rogue Process Selection

Not every process gets scored — only those flagged as potential rogues:

**Automatic inclusion:**
- `state = "stuck"` or `state = "zombie"` — Always a problem
- `pageins > 0` — Any disk I/O for memory is concerning

**Top 3 per category:**
- CPU, Memory, Compressed, Threads, Context Switches, Syscalls

**Expected result:** 10-20 unique processes per sample

### New Data Structures

```python
@dataclass
class ProcessMetrics:
    """Raw metrics for a single process from top."""
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

@dataclass
class ScoredProcess:
    """A process with its calculated stressor score."""
    metrics: ProcessMetrics
    score: int  # 0-100
    categories: frozenset[str]  # Why included

@dataclass
class TopResult:
    """THE canonical format used everywhere."""
    timestamp: float
    process_count: int
    max_score: int
    rogue_processes: list[ScoredProcess]
```

### Tier Transitions

Tiers driven by **max process score** (not system-wide average):

| Tier | Trigger |
|------|---------|
| SENTINEL (1) | max score < 35 |
| ELEVATED (2) | max score ≥ 35 |
| CRITICAL (3) | max score ≥ 65 |

**Rationale:** One rogue process CAN pause a system — it should escalate the whole system.

### Storage Schema (v7)

Simplified: store `TopResult` directly as JSON blob.

```sql
CREATE TABLE event_samples (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    tier INTEGER NOT NULL,
    top_result TEXT NOT NULL,  -- Entire TopResult as JSON
    FOREIGN KEY (event_id) REFERENCES events(id)
);
```

### Configuration Changes

New sections in config.toml:

```toml
[scoring]
cpu = 25
state = 20
pageins = 15
mem = 15
cmprs = 10
csw = 10
sysbsd = 5
threads = 0

[scoring.normalization]
cpu_low = 10
cpu_high = 80
mem_low = 1000000000  # 1GB
mem_high = 8000000000  # 8GB
# ... etc

[tiers]
elevated_threshold = 35  # Was 15
critical_threshold = 65  # Was 50
```

### Implementation Tasks (15 total)

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-4 | Data model & configuration |
| 2 | 5-6 | Data collection (top parsing, TopStream) |
| 3 | 7 | Storage schema v7 |
| 4 | 8-10 | Daemon integration |
| 5 | 11-13 | Minimal TUI & CLI |
| 6 | 14-15 | Cleanup & migration |

---

## TUI Redesign (PLANNED)

> **Status:** Design complete. See `2026-01-23-tui-redesign-design.md`.

### Y-Statement Summary

**In the context of** real-time system monitoring,
**facing** ephemeral process data that disappears before it can be read and a cluttered multi-screen interface,
**we decided for** a single dense btop-style screen with per-process stress scoring and a persistent activity log,
**to achieve** immediate visibility into what's causing system stress,
**accepting** less screen space for historical event management (delegated to CLI).

### Key Changes

1. **Single-screen real-time monitoring** — Everything visible at once, no page switching
2. **Unified process table** — One table sorted by per-process stressor score
3. **Persistent activity log** — Capture threshold crossings so ephemeral spikes are recorded
4. **Per-process stress scoring** — New feature: each process gets 0-80 score
5. **Removed complexity:** EventsScreen (→CLI), EventDetailScreen (→CLI), separate CPU/Pagein tables (→unified)

### Proposed Layout

```
┌─ pause-monitor ─────────────────────────────────────────────────────────────┐
│ STRESS ████████████░░░░░░░░  42/100  [TIER 1]              14:32:07        │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▁▂▃▄▃▂▂▃▅▆▅▄▃▂▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃ (30s)   Load:2.1 Mem:72% Pgin:12/s IO:45M │
├─ PROCESSES (sorted by stress) ──────────────────────────────────────────────┤
│ NAME                 STRESS   CPU ms/s   PAGEINS/s   IO KB/s   WAKEUPS/s   │
│ Chrome                  47      1842          12       2048        145     │
│ mds_stores              23       412           0       4096         23     │
│ kernel_task             18       523           0        128        892     │
│ ...                                                                        │
├─ ACTIVITY LOG ──────────────────────────────────────────────────────────────┤
│ 14:32:05  ▲ Chrome           847 pageins/s                                 │
│ 14:31:58  ▲ mds_stores       12.4 MB/s IO                                  │
│ 14:31:42  ● Tier → ELEVATED  (stress: 67)                                  │
│ ...                                                                        │
├─ RECENT EVENTS ─────────────────────────────────────────────────────────────┤
│ ○ Jan 23 14:28  32s  peak:72   ○ Jan 23 12:15  8s  peak:54   [e] more     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Models (Current - Phase 6)

### events table (Primary - Tier-Based Saving)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| start_timestamp | REAL | Unix timestamp when escalation began |
| end_timestamp | REAL | Unix timestamp when returned to tier 1 (NULL if ongoing) |
| peak_stress | INTEGER | Highest stress score during event |
| peak_tier | INTEGER | Highest tier reached (2 or 3) |
| status | TEXT | "unreviewed", "reviewed", "pinned", "dismissed" |
| notes | TEXT | User-added notes |

### event_samples table (Primary - Tier-Based Saving)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| event_id | INTEGER FK | References events(id) |
| timestamp | REAL | Unix timestamp with ms precision |
| tier | INTEGER | 2=peak save, 3=continuous 10Hz save |
| elapsed_ns | INTEGER | Actual sample interval in nanoseconds |
| throttled | INTEGER | Thermal throttling active |
| cpu_power | REAL | CPU power in milliwatts |
| gpu_pct | REAL | GPU utilization % |
| gpu_power | REAL | GPU power in milliwatts |
| io_read_per_s | REAL | Disk read bytes/sec |
| io_write_per_s | REAL | Disk write bytes/sec |
| wakeups_per_s | REAL | Idle wakeups per second |
| pageins_per_s | REAL | Swap pageins per second |
| stress_total | INTEGER | Combined stress score 0-100 |
| stress_load | INTEGER | Load contribution |
| stress_memory | INTEGER | Memory contribution |
| stress_thermal | INTEGER | Thermal contribution |
| stress_latency | INTEGER | Latency contribution |
| stress_io | INTEGER | I/O contribution |
| stress_gpu | INTEGER | GPU contribution |
| stress_wakeups | INTEGER | Wakeups contribution |
| stress_pageins | INTEGER | Pageins contribution |
| top_cpu_procs | TEXT | JSON array of top 5 CPU processes |
| top_pagein_procs | TEXT | JSON array of top 5 pagein processes |
| top_wakeup_procs | TEXT | JSON array of top 5 wakeup processes |
| top_diskio_procs | TEXT | JSON array of top 5 disk I/O processes |

### samples table (Legacy - Not Used by Tier-Based Saving)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| timestamp | REAL | Unix timestamp with ms precision |
| interval | REAL | Actual seconds since last sample |
| load_avg | REAL | 1-minute load average |
| mem_pressure | INTEGER | Memory pressure level |
| throttled | INTEGER | Thermal throttling active |
| cpu_power | REAL | CPU power in milliwatts |
| gpu_pct | REAL | GPU utilization % |
| gpu_power | REAL | GPU power in milliwatts |
| io_read_per_s | REAL | Disk read bytes/sec |
| io_write_per_s | REAL | Disk write bytes/sec |
| wakeups_per_s | REAL | Idle wakeups per second |
| pageins_per_s | REAL | Swap pageins per second |
| stress_* | INTEGER | Stress breakdown (8 factors) |

### process_samples table (Legacy - Not Used by Tier-Based Saving)
| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| sample_id | INTEGER FK | References samples(id) |
| pid | INTEGER | Process ID |
| name | TEXT | Process name |
| cpu_pct | REAL | Process CPU % |
| mem_pct | REAL | Process memory % |
| io_read | INTEGER | Bytes/sec read |
| io_write | INTEGER | Bytes/sec write |
| energy_impact | REAL | Apple's composite energy score |
| is_suspect | INTEGER | Matches suspect list |

### daemon_state table
| Field | Type | Purpose |
|-------|------|---------|
| key | TEXT PRIMARY KEY | State key |
| value | TEXT | JSON-encoded value |
| updated_at | REAL | Unix timestamp |

Keys: `schema_version`, `io_baseline`, `last_sample_id`

### powermetrics Sample (Runtime)

The parsed output from `powermetrics -f plist` that flows through the daemon. This is the canonical runtime data contract.

```
Top-level dict
├── is_delta: bool (always true for streaming)
├── elapsed_ns: integer (actual sample interval in nanoseconds)
├── timestamp: date (ISO 8601)
├── thermal_pressure: string ("Nominal"|"Moderate"|"Heavy"|"Critical"|"Sleeping")
├── tasks: array (per-process data)
│   └── [each task dict]
│       ├── pid: integer
│       ├── name: string
│       ├── cputime_ms_per_s: real (CPU usage: 1000 = 1 core fully used)
│       ├── intr_wakeups_per_s: real (interrupt-driven wakeups)
│       ├── idle_wakeups_per_s: real (idle wakeups — most relevant for energy)
│       ├── pageins_per_s: real (pages read from disk — KEY pause indicator)
│       ├── diskio_bytesread_per_s: real (per-process disk read)
│       ├── diskio_byteswritten_per_s: real (per-process disk write)
│       └── timer_wakeups: array
│           └── [{interval_ns, wakeups_per_s}, ...]
├── disk: dict (system-wide I/O)
│   ├── rbytes_per_s: real (read bytes/sec)
│   ├── wbytes_per_s: real (write bytes/sec)
│   ├── rops_per_s: real (read ops/sec)
│   └── wops_per_s: real (write ops/sec)
├── processor: dict
│   ├── clusters: array (per-cluster frequency/utilization)
│   ├── cpu_power: real (milliwatts)
│   ├── gpu_power: real (milliwatts)
│   └── combined_power: real (total SoC power in milliwatts)
└── gpu: dict
    ├── freq_hz: real
    ├── idle_ratio: real (1.0 = fully idle, 0.0 = fully busy)
    └── gpu_energy: integer (microjoules per interval)
```

### PowermetricsResult (Python dataclass)

The parsed/aggregated data structure used internally:

```python
@dataclass
class PowermetricsResult:
    # Timing
    elapsed_ns: int                     # Actual sample interval

    # Thermal
    throttled: bool                     # True if thermal_pressure != "Nominal"

    # Power
    cpu_power: float | None             # Milliwatts
    gpu_power: float | None             # Milliwatts

    # GPU
    gpu_pct: float | None               # (1 - idle_ratio) * 100

    # System-wide aggregates (for stress calculation)
    io_read_per_s: float                # bytes/sec from disk dict
    io_write_per_s: float               # bytes/sec from disk dict
    wakeups_per_s: float                # Sum of tasks[].idle_wakeups_per_s
    pageins_per_s: float                # Sum of tasks[].pageins_per_s

    # Top 5 per-process (for culprit identification)
    top_cpu_processes: list[dict]       # [{name, pid, cpu_ms_per_s}]
    top_pagein_processes: list[dict]    # [{name, pid, pageins_per_s}]
    top_wakeup_processes: list[dict]    # [{name, pid, wakeups_per_s}]
    top_diskio_processes: list[dict]    # [{name, pid, diskio_per_s}] read+write
```

**Culprit identification mapping:**

| Stress Factor | Aggregate Source | Top 5 Process Source |
|---------------|------------------|----------------------|
| load | psutil load_avg | top_cpu_processes |
| memory | psutil mem_pressure | by_memory (snapshots) |
| thermal | throttled | — (system-wide) |
| latency | elapsed_ns | — (system-wide) |
| io | io_read + io_write | top_diskio_processes |
| gpu | gpu_pct | — (no per-process) |
| wakeups | wakeups_per_s | top_wakeup_processes |
| pageins | pageins_per_s | top_pagein_processes |

## Configuration

Location: `~/.config/pause-monitor/config.toml`

| Section | Option | Default | Purpose |
|---------|--------|---------|---------|
| (root) | learning_mode | false | Collect data without alerts during calibration |
| sampling | normal_interval | 5 | Seconds between samples (normal mode) |
| sampling | elevated_interval | 1 | Seconds between samples (elevated mode) |
| sampling | elevation_threshold | 30 | Stress score to enter elevated mode |
| sampling | critical_threshold | 60 | Stress score for preemptive snapshot |
| **sentinel** | **fast_interval_ms** | **100** | **Fast loop interval (ms)** |
| **sentinel** | **ring_buffer_seconds** | **30** | **Ring buffer history size** |
| **tiers** | **elevated_threshold** | **15** | **Enter ELEVATED tier** |
| **tiers** | **critical_threshold** | **50** | **Enter CRITICAL tier** |
| retention | samples_days | 30 | Days to keep samples |
| retention | events_days | 90 | Days to keep events |
| alerts | enabled | true | Master switch for alerts |
| alerts | pause_detected | true | Alert on pause events |
| alerts | pause_min_duration | 2.0 | Minimum pause duration to alert |
| alerts | critical_stress | true | Alert on sustained critical stress |
| alerts | critical_threshold | 60 | Stress level for critical alert |
| alerts | critical_duration | 30 | Seconds stress must be sustained |
| alerts | elevated_entered | false | Alert when entering elevated mode |
| alerts | forensics_completed | true | Alert when forensics capture finishes |
| alerts | sound | true | Play sound with notifications |
| suspects | patterns | [...] | Process name patterns to flag |

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pause-monitor daemon` | Run background sampler (foreground) |
| `pause-monitor tui` | Launch interactive dashboard |
| `pause-monitor status` | Quick health check (one-liner) |
| `pause-monitor events` | List pause events (supports --status filter) |
| `pause-monitor events <id>` | Inspect specific event |
| `pause-monitor events mark <id> <status>` | Change event status (reviewed/pinned/dismissed) |
| `pause-monitor history` | Query historical data |
| `pause-monitor history --at "<time>"` | Show what was happening at a specific time |
| `pause-monitor config` | Manage configuration |
| `pause-monitor prune` | Manual data cleanup |
| `pause-monitor install` | Set up launchd service, sudoers, tailspin |
| `pause-monitor uninstall` | Remove launchd service |
| `pause-monitor calibrate` | Show suggested thresholds from learning mode |

## Workflows

### Sampling Loop (Current)
1. Run powermetrics at 100ms intervals (streaming)
2. Parse each plist sample
3. Calculate stress score
4. Check if elevated mode should change (hysteresis: elevate at threshold, de-elevate after 5s below)
5. Push to ring buffer and broadcast via socket
6. If tier 2+, save event samples to SQLite
7. If pause detected (interval > 2x expected), trigger forensics
8. If critical stress (>50), trigger preemptive snapshot

### Sampling Loop (Planned - Per-Process)
1. Run top at 1Hz intervals
2. Parse output, extract all ~400 processes
3. Select rogues (automatic inclusion + top 3 per category)
4. Score each rogue (8 weighted factors)
5. Use max_score for tier transitions
6. Push TopResult to ring buffer and broadcast
7. If tier 2+, save TopResult JSON to SQLite
8. Forensics unchanged

### Pause Detection
1. Compare actual interval vs expected interval
2. If ratio > 2.0, check if system recently woke from sleep
3. If NOT sleep: record as pause event, trigger forensics
4. If sleep: log as sleep event, don't record as pause

### Forensics Capture (on pause)
1. Immediate process snapshot via psutil (~50ms)
2. Save tailspin trace (~1s, privileged)
3. Disk I/O snapshot via powermetrics (~1s, privileged)
4. Trigger spindump (~5-10s, privileged)
5. Thermal snapshot via powermetrics (~1s, privileged)
6. Extract system logs (~1s)
7. Write summary.json

### Install Process
1. Verify not running as root
2. Check prerequisites (macOS 12+, admin group)
3. Create data directories
4. Initialize database
5. Install sudoers rules (validates with visudo)
6. Enable tailspin
7. Install launchd plist
8. Validate forensics health

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Stress scoring over CPU thresholds | High CPU alone doesn't indicate problems; what matters is contention |
| Always 1s sampling, variable storage | Avoids restarting powermetrics when switching modes; stress calculation still happens every second |
| Hysteresis for mode transitions | Elevate at threshold, de-elevate after 5s below to prevent rapid mode cycling |
| WAL mode for SQLite | Allows concurrent daemon writes and TUI reads |
| tailspin for kernel traces | Only way to see kernel-level activity during freezes |
| pmset for sleep detection | No pyobjc dependency, works reliably, provides wake type |
| caffeinate over NSProcessInfo | No pyobjc required, simple subprocess approach |
| terminal-notifier over osascript | Better UX when available, osascript fallback always works |
| Privileged mode required | Per-process I/O is essential for identifying culprits; not optional |
| User-specific sudoers rules | Constrain output paths to prevent cross-user attacks |
| Unix socket for TUI streaming | Real-time data without SQLite polling overhead |
| TierManager extracted from Sentinel | Reusable state machine; Sentinel class deleted in Phase 5 |
| **Per-process scoring (planned)** | Attribution built-in; flip from "detect then hunt" to "always know the culprits" |
| **1Hz with top (planned)** | Complete per-process metrics vs partial from powermetrics |
| **Max score for tiers (planned)** | One rogue CAN pause a system; should escalate the whole system |

## Privileged Operations

Required for full functionality (via sudoers):

| Operation | Command | Purpose |
|-----------|---------|---------|
| Per-process I/O | `powermetrics --show-process-io` | Identify disk I/O culprits |
| Kernel traces | `tailspin save` | Capture activity during freeze |
| Thread stacks | `spindump` | Post-pause forensics |
| Thermal data | `powermetrics --samplers thermal` | Detect throttling |

## Data Locations

| Purpose | Path |
|---------|------|
| Config | `~/.config/pause-monitor/config.toml` |
| Database | `~/.local/share/pause-monitor/data.db` |
| Events | `~/.local/share/pause-monitor/events/` |
| Daemon log | `~/.local/share/pause-monitor/daemon.log` |
| PID file | `~/.local/share/pause-monitor/daemon.pid` |
| Socket | `~/.local/share/pause-monitor/daemon.sock` |
| launchd plist | `~/Library/LaunchAgents/com.local.pause-monitor.plist` |
| sudoers | `/etc/sudoers.d/pause-monitor-<username>` |

## Dependencies

| Package | Purpose |
|---------|---------|
| textual | Modern TUI framework |
| rich | Pretty CLI output |
| click | CLI framework |
| aiosqlite | Async SQLite for TUI |
| structlog | Structured daemon logging |
| tomlkit | TOML config with comment preservation |

Not required: psutil (powermetrics provides all metrics), pyobjc (pmset + clock drift for sleep detection)
