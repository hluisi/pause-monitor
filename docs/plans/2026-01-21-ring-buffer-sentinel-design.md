# Ring Buffer Sentinel Design

**Date:** 2026-01-21
**Status:** Approved

## Overview

### Problem

The current pause monitor samples at 5-second intervals during normal operation. When a pause is detected, we have no visibility into what was happening in the seconds leading up to it. We're blind to the buildup.

### Solution

A lightweight stress sentinel that runs continuously at 100ms intervals, storing results in a ring buffer. This provides 30 seconds of history at all times. When stress rises, we escalate to capture more detail. When a pause occurs, we have the full story.

### Key Principles

- **Cheap always-on monitoring** — Stress calculation via sysctl costs ~20µs per sample (0.02% CPU at 10Hz)
- **Escalate on demand** — Heavier collection only when stress warrants it
- **Never lose context** — Ring buffer ensures we always have pre-incident history
- **Events have lifecycle** — Unreviewed events can't be pruned; reviewed events can be dismissed or pinned

### What This Replaces

- The current adaptive sampling (5s normal / 1s elevated) becomes the ring buffer sentinel
- `powermetrics` streaming remains for detailed metrics but is no longer the primary sampling mechanism
- Forensics capture gains the ring buffer as a new data source

---

## Tiered Monitoring Architecture

### Tier 1: Sentinel (stress < 15)

Always running. Hybrid polling:

**Fast loop (100ms / 10Hz):**
- `os.getloadavg()` — load average
- `sysctlbyname("kern.memorystatus_level")` — memory pressure
- `sysctlbyname("vm.page_free_count")` — available memory
- `time.monotonic()` — for latency ratio
- IOKit — disk I/O counters
- Uses latest GPU/wakeups/thermal from slow loop

**Slow loop (1s / 1Hz):**
- GPU usage via lightweight powermetrics
- Idle wakeups via powermetrics
- Thermal throttling via powermetrics
- Updates cached values for fast loop

Output: `StressBreakdown` (7 integers) + timestamp. Stored in ring buffer. No persistence, no logging.

### Tier 2: Elevated (stress 15-50)

Triggered when `stress.total >= 15`. Adds:

- Process snapshot on entry (top 10 by CPU, top 10 by memory)
- Continued stress sampling to ring buffer
- Process snapshot at new peak (if stress exceeds previous max)
- Log entry: "Elevated incident started at HH:MM:SS, stress=X"

On de-escalation (stress drops below 15 for 5+ seconds):

- Process snapshot on exit
- Log entry with duration, peak stress, summary
- Incident record saved for trend analysis

### Tier 3: Critical (stress >= 50)

Triggered when `stress.total >= 50`. Adds:

- Preemptive forensics (spindump, current process stacks)
- More frequent process snapshots (every 5 seconds while critical)
- All data feeds into ring buffer for preservation

---

## Expanded Stress Model

### Stress Factors (7 total)

| Factor | Source | Max Points | Threshold |
|--------|--------|------------|-----------|
| Load | `os.getloadavg()` | 40 | load/cores > 1.0 |
| Memory | `sysctlbyname` | 30 | available < 20% |
| Thermal | powermetrics | 20 | throttling active |
| Latency | self-measured | 30 | actual/expected > 1.5 |
| I/O | IOKit | 20 | 10x baseline spike |
| GPU | powermetrics | 20 | usage > 80% sustained |
| Idle wakeups | powermetrics | 20 | > 1000/sec sustained |

### Updated StressBreakdown

```python
@dataclass
class StressBreakdown:
    load: int       # 0-40
    memory: int     # 0-30
    thermal: int    # 0-20
    latency: int    # 0-30
    io: int         # 0-20
    gpu: int        # 0-20
    wakeups: int    # 0-20

    @property
    def total(self) -> int:
        return min(100, self.load + self.memory + self.thermal +
                   self.latency + self.io + self.gpu + self.wakeups)
```

### Forensic-Only Signals (captured but not scored)

| Signal | Source | Why It Matters |
|--------|--------|----------------|
| Zombie count | psutil | Broken process lifecycle |
| Purgeable memory | `sysctlbyname` | System could free RAM but hasn't |
| Swap used | `sysctlbyname` | Already in Sample, useful context |

---

## Ring Buffer Design

### Data Structures

```python
@dataclass
class RingSample:
    timestamp: datetime
    stress: StressBreakdown
    tier: int  # 1, 2, or 3 at time of capture

@dataclass
class ProcessSnapshot:
    timestamp: datetime
    trigger: str  # "tier2_entry", "tier2_peak", "tier2_exit", "tier3_periodic"
    by_cpu: list[ProcessInfo]   # top 10
    by_memory: list[ProcessInfo]  # top 10

@dataclass
class ProcessInfo:
    pid: int
    name: str
    cpu_pct: float
    memory_mb: float
```

### Ring Buffer Contents

- `samples: deque[RingSample]` — maxlen=300 (30 seconds at 100ms)
- `snapshots: list[ProcessSnapshot]` — process snapshots from tier 2/3

### Operations

- `push(sample)` — add stress sample, auto-evicts oldest
- `snapshot_processes(trigger)` — capture top processes, store with trigger reason
- `freeze() -> BufferContents` — return copy of all samples + snapshots for event capture
- `clear_snapshots()` — called on de-escalation after incident logged

### Memory Footprint

- 300 samples × ~50 bytes = ~15KB
- Process snapshots: ~2KB each, maybe 3-5 during an elevated incident
- Total: < 30KB typical

---

## Pause Detection & Event Capture

### Flow

```
PAUSE DETECTED
     │
     ├─── What tier were we in?
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│  1. FREEZE RING BUFFER                                      │
│     • Copy all 300 stress samples                           │
│     • Copy all process snapshots                            │
│     • Note which tier we were in when pause hit             │
│                                                             │
│  2. IDENTIFY CULPRITS (from ring buffer)                    │
│     • Which stress factors were elevated?                   │
│     • Which processes appeared in snapshots?                │
│     • Correlate: high memory stress → memory hogs           │
│                                                             │
│  3. POST-PAUSE CAPTURE                                      │
│     • spindump (thread stacks - what's stuck now)           │
│     • system logs from pause window                         │
│     • current process list (what's running now)             │
│                                                             │
│  4. CREATE EVENT                                            │
│     • Combine: ring buffer + culprits + forensics           │
│     • Status: UNREVIEWED                                    │
│     • Write to events directory                             │
│     • Insert record into database                           │
└─────────────────────────────────────────────────────────────┘
```

### Culprit Identification

Look at the stress breakdown over the last 30 seconds:

- If `memory` scores were high → flag top memory consumers from snapshots
- If `load` scores were high → flag top CPU consumers
- If `io` scores were high → flag processes with high disk activity
- If `gpu` scores were high → flag GPU-intensive processes
- If `wakeups` scores were high → flag processes with high idle wakeups
- If no snapshots (pause from tier 1) → capture process list post-pause, note "no pre-pause process data"

---

## Event Storage & Lifecycle

### Event Directory Structure

```
~/.local/share/pause-monitor/events/
└── 2026-01-21T14-32-01/
    ├── metadata.json           # Event info, status, culprits
    ├── ring_buffer.json        # 30s of stress samples
    ├── process_snapshots.json  # Entry/peak/exit snapshots
    ├── spindump.txt            # Thread stacks at pause
    ├── system.log              # Filtered logs from window
    └── forensics/              # Any additional captures
```

### metadata.json

```json
{
  "id": "2026-01-21T14-32-01",
  "timestamp": "2026-01-21T14:32:01.423Z",
  "pause_duration": 12.4,
  "tier_at_pause": 2,
  "peak_stress": 47,
  "status": "unreviewed",
  "culprits": [
    {"factor": "memory", "score": 28, "processes": ["Chrome", "Electron"]},
    {"factor": "gpu", "score": 15, "processes": ["WindowServer"]}
  ],
  "notes": null
}
```

### Event States

| State | Meaning | Prunable? |
|-------|---------|-----------|
| `unreviewed` | New, hasn't been looked at | No |
| `reviewed` | Someone looked at it | Configurable |
| `pinned` | Explicitly kept for reference | No |
| `dismissed` | Looked at, not interesting | Yes |

### Database Schema

```sql
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    pause_duration REAL NOT NULL,
    tier_at_pause INTEGER NOT NULL,
    peak_stress INTEGER NOT NULL,
    status TEXT DEFAULT 'unreviewed',
    event_dir TEXT NOT NULL,
    notes TEXT
);
```

### Pruning Logic

```python
def prune_events(retention_days: int, conn: Connection) -> int:
    """Delete old events, respecting lifecycle status."""
    cutoff = datetime.now() - timedelta(days=retention_days)

    # Only prune 'reviewed' or 'dismissed' events
    # Never prune 'unreviewed' or 'pinned'
    prunable = conn.execute("""
        SELECT id, event_dir FROM events
        WHERE timestamp < ? AND status IN ('reviewed', 'dismissed')
    """, (cutoff,)).fetchall()

    for event_id, event_dir in prunable:
        shutil.rmtree(event_dir)
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

    return len(prunable)
```

---

## CLI & TUI Interface

### CLI Commands

```bash
# List events with status
pause-monitor events                     # All events, newest first
pause-monitor events --status unreviewed # Filter by status
pause-monitor events --since 7d          # Last 7 days

# View event details
pause-monitor events <id>                # Summary
pause-monitor events <id> --full         # Full ring buffer + snapshots

# Change event status
pause-monitor events mark <id> --reviewed
pause-monitor events mark <id> --pinned
pause-monitor events mark <id> --dismissed
pause-monitor events mark <id> --notes "Chrome memory leak, reported upstream"

# Bulk operations
pause-monitor events mark --all-reviewed --dismissed
pause-monitor events mark --older-than 30d --dismissed
```

### TUI Events Panel

```
┌─ Pause Monitor ─────────────────────────────────────────────┐
│                                                             │
│  [Live] [Events] [History]                     stress: 12   │
│  ───────────────────────────────────────────────────────────│
│                                                             │
│  EVENTS (3 unreviewed)                                      │
│                                                             │
│  ● 2026-01-21 14:32  12.4s pause  peak:47  [unreviewed]    │
│    └─ culprits: memory (Chrome, Electron), gpu (WindowSvr) │
│                                                             │
│  ○ 2026-01-20 09:15   3.2s pause  peak:31  [reviewed]      │
│    └─ culprits: load (kernel_task)                         │
│                                                             │
│  ◆ 2026-01-18 22:41   8.7s pause  peak:62  [pinned]        │
│    └─ culprits: io (Spotlight), memory (Safari)            │
│                                                             │
│  ───────────────────────────────────────────────────────────│
│  [r]eview  [p]in  [d]ismiss  [enter]details  [q]uit        │
└─────────────────────────────────────────────────────────────┘
```

### TUI Event Detail View

```
┌─ Event: 2026-01-21T14-32-01 ────────────────────────────────┐
│                                                             │
│  Duration: 12.4s    Tier at pause: 2    Peak stress: 47    │
│  Status: unreviewed                                         │
│                                                             │
│  STRESS TIMELINE (30s before pause)                        │
│  ────────────────────────────────────                      │
│  stress ▁▁▂▂▃▃▅▅▆▇▇███████████████░░░░░ pause              │
│  memory ▁▁▁▂▃▄▅▆▇███████████████████░░░                    │
│  gpu    ▁▁▁▁▁▂▂▃▃▄▄▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅░░░░                    │
│                                                             │
│  CULPRITS                                                   │
│  ────────                                                   │
│  memory (28 pts): Chrome (2.1GB), Electron (890MB)         │
│  gpu (15 pts): WindowServer (45%)                          │
│                                                             │
│  PROCESS SNAPSHOTS                                          │
│  ─────────────────                                          │
│  [tier2_entry] [tier2_peak] [pause]                        │
│                                                             │
│  ───────────────────────────────────────────────────────────│
│  [r]eview  [p]in  [d]ismiss  [n]otes  [s]pindump  [b]ack   │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary

### New Modules

| Module | Purpose |
|--------|---------|
| `sentinel.py` | Tier 1 stress polling (10Hz fast loop, 1Hz slow loop) |
| `ringbuffer.py` | Ring buffer + process snapshot storage |
| `incident.py` | Elevated incident tracking, close-out on de-escalation |

### Modules to Modify

| Module | Changes |
|--------|---------|
| `stress.py` | Add `gpu` and `wakeups` to `StressBreakdown`; update `calculate_stress()` |
| `daemon.py` | Replace adaptive sampling with sentinel; integrate ring buffer; update pause handling |
| `collector.py` | Implement `_get_io_counters()` (currently stubbed); add zombie/purgeable collection |
| `storage.py` | Add `status` column to events table; update pruning logic |
| `forensics.py` | Accept ring buffer contents; include in event capture |
| `config.py` | Add tier thresholds (15/50), ring buffer size, sentinel intervals |
| `cli.py` | Add event status management commands |
| `tui/` | Add events panel, event detail view, keyboard shortcuts |

### Configuration

```toml
[sentinel]
fast_interval_ms = 100
slow_interval_ms = 1000
ring_buffer_seconds = 30

[tiers]
elevated_threshold = 15
critical_threshold = 50

[events]
default_retention_days = 30
protect_unreviewed = true
protect_pinned = true
```

### Migration Notes

- Existing events table needs `status` column (default: `'reviewed'` for old events)
- Old adaptive sampling config (`normal_interval`, `elevated_interval`) becomes deprecated
- `powermetrics` streaming continues but is no longer the primary sampling driver
