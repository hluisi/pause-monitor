---
id: architecture-systems
type: architecture
domain: project
subject: systems
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [systems]
tags: []
related: []
sources: []
---

# Architectural Systems

This inventory documents ALL reusable infrastructure. **Read before writing code** to avoid recreating existing systems.

---

## 1. CONFIGURATION (`config.py`)

**Purpose:** Single source of truth for all configurable parameters

### Key Classes

| Class | Purpose |
|-------|---------|
| `Config` | Main container, loads/saves TOML |
| `SystemConfig` | ring_buffer_size, sample_interval, forensics_debounce |
| `BandsConfig` | Score thresholds (medium/elevated/high/critical), tracking/forensics bands |
| `ScoringConfig` | Resource weights, state multipliers |
| `RogueSelectionConfig` | Display selection (score_threshold, max_count) |
| `TUIConfig` | Colors, sparkline settings |

### Paths (XDG compliant)

```python
config.config_path   # ~/.config/rogue-hunter/config.toml
config.db_path       # ~/.local/share/rogue-hunter/data.db
config.log_path      # ~/.local/state/rogue-hunter/daemon.log
config.socket_path   # /tmp/rogue-hunter/daemon.sock
config.runtime_dir   # /tmp/rogue-hunter/
```

### Usage

```python
from rogue_hunter.config import Config

config = Config.load()  # Auto-creates if missing

# Access values
interval = config.system.sample_interval      # 0.333 (3Hz)
threshold = config.bands.tracking_threshold   # Score where tracking starts
band = config.bands.get_band(score=55)        # Returns "elevated"

# Access paths
db = config.db_path
```

### Anti-Patterns

- **DON'T:** Hardcode thresholds, intervals, retention days, or paths
- **USE:** `config.bands.get_band(score)` instead of writing band logic
- **USE:** `config.db_path` instead of hardcoded paths

---

## 2. LOGGING (`logging.py`)

**Purpose:** Unified console + JSON file output

### Console Output (Rich)

```python
from rogue_hunter import logging as rlog

rlog.info("message")
rlog.warn("message")
rlog.error("message")
rlog.info("message", icon=rlog.Icon.OK)  # ✓ message
```

### Icons

`Icon.OK` (✓), `Icon.FAIL` (✗), `Icon.WAIT` (⏳), `Icon.CAPTURE` (📸), `Icon.PRUNE` (🧹), `Icon.HEARTBEAT` (♡), `Icon.ROGUE_ENTER` (▲), `Icon.ROGUE_EXIT` (▼), `Icon.SIGNAL` (⚡), `Icon.CONNECTED` (⬤), `Icon.DISCONNECTED` (⬤)

### Domain Helpers

```python
rlog.daemon_started()
rlog.rogue_enter(cmd, pid, score, metrics)
rlog.rogue_exit(cmd, pid)
rlog.heartbeat(avg_score, max_score, tracked_count, ...)
rlog.forensics_captured(event_id, capture_id)
```

### Structlog (JSON file)

```python
log = rlog.get_structlog()
log.info("event_name", key="value")  # → ~/.local/state/rogue-hunter/daemon.log
```

### Anti-Patterns

- **DON'T:** Use `print()` anywhere
- **DON'T:** Use `logging.` module directly
- **USE:** `rlog.info()` for console, `log.info()` for structured events

---

## 3. STORAGE (`storage.py`)

**Purpose:** SQLite with WAL, schema v18

### Database Access

```python
from rogue_hunter.storage import require_database, DatabaseNotAvailable

with require_database(config.db_path) as conn:
    # Use connection
    pass
```

### Key Tables

| Table | Purpose |
|-------|---------|
| `process_events` | Event lifecycle (entry/exit times, peak score) |
| `process_snapshots` | Full ProcessScore at entry/exit/checkpoint |
| `forensic_captures` | Capture metadata (trigger, status) |
| `spindump_processes` | Parsed process data |
| `log_entries` | System log entries |
| `buffer_context` | Ring buffer state at capture |

### CRUD Functions

```python
from rogue_hunter.storage import (
    create_process_event, close_process_event, update_process_event_peak,
    insert_process_snapshot, get_process_snapshots,
    get_process_events, get_open_events,
    create_forensic_capture, prune_old_data
)

event_id = create_process_event(conn, pid, command, boot_time, entry_time, ...)
insert_process_snapshot(conn, event_id, "entry", process_score)
close_process_event(conn, event_id, exit_time)
```

### Anti-Patterns

- **DON'T:** Execute raw SQL outside storage.py
- **DON'T:** Migrate schema; mismatch triggers fresh database
- **USE:** Provided CRUD functions

---

## 4. PROCESS DATA (`collector.py`)

**Purpose:** THE canonical process data schema

### ProcessScore (Canonical Schema)

```python
@dataclass
class ProcessScore:
    # Identity
    pid: int
    command: str
    captured_at: float
    
    # Metrics (39 fields)
    cpu: float              # Percentage
    mem: int                # Bytes
    disk_io: int            # Cumulative bytes
    gpu_time: int           # Nanoseconds
    wakeups: int            # Cumulative
    # ... (rates, contention, efficiency, etc.)
    
    # Scoring (v18)
    score: int              # 0-100
    band: str               # "low"/"medium"/"elevated"/"high"/"critical"
    cpu_share: float        # Share of system CPU
    gpu_share: float
    mem_share: float
    disk_share: float
    wakeups_share: float
    disproportionality: float    # max(all shares)
    dominant_resource: str       # Which resource is highest
```

### ProcessSamples (Batch)

```python
@dataclass
class ProcessSamples:
    timestamp: datetime
    elapsed_ms: int
    process_count: int
    max_score: int
    rogues: list[ProcessScore]  # Top N by score
```

### Collection

```python
from rogue_hunter.collector import LibprocCollector

collector = LibprocCollector(config)
samples = await collector.collect()  # Returns ProcessSamples
```

### Anti-Patterns

- **DON'T:** Create alternative process data structures
- **DON'T:** Calculate shares manually
- **USE:** `ProcessScore` everywhere for process data
- **USE:** `score.band` to categorize, not raw score

---

## 5. EVENT TRACKING (`tracker.py`)

**Purpose:** Per-process event lifecycle with snapshots

### Lifecycle

```
1. score >= tracking_threshold → opens event + entry snapshot
2. While tracked: periodic checkpoints, peak updates
3. score < threshold for N samples → closes event + exit snapshot
```

### Usage

```python
from rogue_hunter.tracker import ProcessTracker

tracker = ProcessTracker(conn, config.bands, boot_time, on_forensics_trigger=...)
tracker.update(samples.rogues)  # Call each sample cycle
```

### Anti-Patterns

- **DON'T:** Create events manually; tracker manages lifecycle
- **DON'T:** Hardcode checkpoint intervals; use config

---

## 6. RING BUFFER (`ringbuffer.py`)

**Purpose:** 30 seconds of pre-incident context

```python
from rogue_hunter.ringbuffer import RingBuffer

buffer = RingBuffer(max_samples=60)
buffer.push(samples)
frozen = buffer.freeze()  # Immutable snapshot for forensics
```

---

## 7. IPC SOCKET (`socket_server.py`, `socket_client.py`)

**Purpose:** Real-time streaming daemon → TUI

### Protocol

- **Transport:** Unix socket at `/tmp/rogue-hunter/daemon.sock`
- **Format:** Newline-delimited JSON
- **Direction:** Push-based (daemon broadcasts, TUI listens)

### Message Types

| Type | Direction | Contents |
|------|-----------|----------|
| `initial_state` | Daemon → TUI | History, sample_count |
| `sample` | Daemon → TUI | ProcessSamples |
| `log` | TUI → Daemon | Log forwarding |

### Usage

```python
# Daemon
server = SocketServer(config.socket_path, ring_buffer)
await server.start()
await server.broadcast(samples)

# TUI
client = SocketClient(config.socket_path)
await client.connect()
msg = await client.read_message()
```

---

## 8. FORENSICS (`forensics.py`)

**Purpose:** Automatic diagnostic capture (tailspin, logs)

### Capture Flow

1. `sudo -n tailspin save` + `log show` in parallel
2. Decode via `spindump -i`
3. Parse and store in database
4. Cleanup temp files

### Usage

```python
from rogue_hunter.forensics import ForensicsCapture

capture = ForensicsCapture(conn, event_id, config.runtime_dir)
capture_id = await capture.capture_and_store(buffer.freeze(), "band_entry_critical")
```

### Anti-Patterns

- **DON'T:** Capture without debounce check (tracker handles)
- **DON'T:** Run tailspin without sudoers setup

---

## 9. SYSTEM BINDINGS

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `libproc.py` | macOS libproc.dylib | `list_all_pids()`, `get_rusage()`, `get_task_info()` |
| `iokit.py` | GPU metrics | `get_gpu_usage()` → dict[pid, ns] |
| `sysctl.py` | System tuning | `sysctl_int("hw.ncpu")` |
| `boottime.py` | Boot timestamp | `get_boot_time()` |
| `sleepwake.py` | Sleep/wake detection | `was_recently_asleep()`, `get_recent_sleep_events()` |

---

## 10. TUI (`tui/app.py`, `tui/sparkline.py`)

**Purpose:** Textual-based interactive dashboard

- `RogueHunterApp`: Main application
- `Sparkline`: Custom widget for metric visualization

---

## 11. EXCEPTIONS

```python
DatabaseNotAvailable  # Database not found (storage.py)
ValueError            # Config validation failures
RuntimeError          # Daemon already running
```

---

## 12. INTEGRATION PATTERNS

### Data Flow

```
libproc.dylib → LibprocCollector → ProcessSamples
                      ↓
              ProcessTracker → Storage (SQLite)
                      ↓
              SocketServer → TUI (Unix socket)
```

### Configuration Flow

```
Config.load() → passed to Daemon, Tracker, Collector
```
No hot-reload; changes require daemon restart.

---

## 13. GAPS (Systems That Don't Exist)

| Gap | Impact | Priority |
|-----|--------|----------|
| Hot-reload config | Requires daemon restart | Medium |
| Advanced event queries | CLI is basic | Low |

---

## QUICK REFERENCE

| Need to... | Use |
|------------|-----|
| Get a config value | `config.system.sample_interval` |
| Get a path | `config.db_path`, `config.socket_path` |
| Log to console | `rlog.info("msg")` |
| Log structured event | `log.info("event", key=val)` |
| Access database | `with require_database(path) as conn:` |
| Create event | `create_process_event(conn, ...)` |
| Get process data | `collector.collect()` → `ProcessSamples` |
| Track events | `tracker.update(scores)` |
| Stream to TUI | `server.broadcast(samples)` |
| Capture forensics | `ForensicsCapture(...).capture_and_store()` |
