# Implementation Guide

**Last updated:** 2026-01-31
**Schema version:** 14

## Overview

**Rogue Hunter** is a real-time process surveillance tool for macOS that identifies processes negatively affecting system performance. It scores all processes on four dimensions of rogue behavior (blocking, contention, pressure, efficiency) and tracks those that cross configurable thresholds.

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
┌─────────────────────────────────────┐   ┌──────────────────────────────────┐
│           DAEMON (daemon.py)        │   │          TUI (tui/app.py)        │
│                                     │   │                                  │
│  ┌─────────────┐   ┌─────────────┐  │   │  ┌──────────┐  ┌─────────────┐  │
│  │LibprocColl- │   │ProcessTrack-│  │   │  │HeaderBar │  │ProcessTable │  │
│  │ector        │   │er           │  │   │  │(sparkline)│  │             │  │
│  │(collector.py│   │(tracker.py) │  │   │  └──────────┘  └─────────────┘  │
│  │)            │   │             │  │   │  ┌──────────┐  ┌─────────────┐  │
│  └──────┬──────┘   └──────┬──────┘  │   │  │Activity  │  │TrackedEvents│  │
│         │                 │         │   │  │Log       │  │Panel        │  │
│         ▼                 ▼         │   │  └──────────┘  └─────────────┘  │
│  ┌─────────────────────────────┐    │   │                                  │
│  │      RingBuffer             │    │   └──────────────────────────────────┘
│  │      (ringbuffer.py)        │    │                 ▲
│  └─────────────┬───────────────┘    │                 │
│                │                    │                 │ JSON over Unix socket
│                ▼                    │                 │
│  ┌─────────────────────────────┐    │   ┌────────────┴────────────┐
│  │    SocketServer             │◄───┼───│     SocketClient        │
│  │    (socket_server.py)       │    │   │     (socket_client.py)  │
│  └─────────────────────────────┘    │   └─────────────────────────┘
│                                     │
│  ┌─────────────────────────────┐    │
│  │   ForensicsCapture          │    │
│  │   (forensics.py)            │    │
│  │   • tailspin                │    │
│  │   • log show                │    │
│  └─────────────────────────────┘    │
│                │                    │
└────────────────┼────────────────────┘
                 ▼
        ┌────────────────────────────────────────────┐
        │              SQLite (storage.py)           │
        │                                            │
        │  daemon_state │ process_events │ snapshots │
        │  forensic_captures │ spindump_* │ log_*   │
        └────────────────────────────────────────────┘
```

### Data Flow

1. **Collection:** `LibprocCollector` calls libproc.dylib APIs to gather per-process metrics
2. **Scoring:** Each process gets a weighted stress score (0-100) with category tags
3. **Rogue Selection:** Top-N processes by score become "rogues" for display/tracking
4. **Ring Buffer:** ProcessSamples pushed to buffer for historical context
5. **Low/High Enrichment:** Daemon computes per-metric low/high from ring buffer history
6. **Tracking:** ProcessTracker opens/updates/closes events for processes in "bad" bands
7. **Forensics:** When process enters high band, forensic capture is triggered
8. **Broadcast:** SocketServer sends enriched samples to TUI clients
9. **Display:** TUI updates widgets from socket messages (no DB queries)

---

## Module: libproc.py

**Purpose:** ctypes bindings to macOS libproc.dylib for direct process inspection.

### Key Types
| Type | Purpose |
|------|---------|
| `RusageInfoV4` | ctypes Structure for rusage_info_v4 |
| `ProcTaskInfo` | ctypes Structure for proc_taskinfo |
| `ProcBSDInfo` | ctypes Structure for proc_bsdinfo |
| `TimebaseInfo` | Mach timebase for Apple Silicon time conversion |

### Key Functions
| Function | Purpose |
|----------|---------|
| `list_all_pids()` | Get list of all PIDs on system |
| `get_rusage(pid)` | CPU time, memory, disk I/O, energy, wakeups |
| `get_task_info(pid)` | Context switches, syscalls, threads, Mach messages |
| `get_bsd_info(pid)` | Process state, command name, priority |
| `get_process_name(pid)` | Get process name from PID |
| `get_state_name(state)` | Convert state code to name |
| `get_timebase_info()` | Apple Silicon time unit conversion |
| `abs_to_ns(abstime)` | Convert mach_absolute_time to nanoseconds |

### State Constants
| Constant | Value | Name |
|----------|-------|------|
| `SIDL` | 1 | idle |
| `SRUN` | 2 | running |
| `SSLEEP` | 3 | sleeping |
| `SSTOP` | 4 | stopped |
| `SZOMB` | 5 | zombie |

---

## Module: collector.py

**Purpose:** Per-process data collection via libproc.dylib with stress scoring.

### Key Types
| Type | Purpose |
|------|---------|
| `MetricValue` | Numeric metric with current/low/high range |
| `MetricValueStr` | String metric with current/low/high range |
| `ProcessScore` | THE canonical process data schema (all metrics + scoring) |
| `ProcessSamples` | Collection batch with timestamp, rogues, max_score |
| `_PrevSample` | Internal: previous CPU time for delta calculation |
| `LibprocCollector` | Main collector class |

### MetricValue Fields
```python
@dataclass
class MetricValue:
    current: float | int  # Current sample value
    low: float | int      # Minimum in ring buffer window
    high: float | int     # Maximum in ring buffer window
```

### ProcessScore Fields (the canonical schema)

**Identity:** `pid`, `command`, `captured_at`

**CPU:** `cpu` (MetricValue)

**Memory:** `mem`, `mem_peak`, `pageins`, `pageins_rate`, `faults`, `faults_rate` (5 MetricValue + 1 int)

**Disk I/O:** `disk_io`, `disk_io_rate` (2 MetricValue)

**Activity:** `csw`, `csw_rate`, `syscalls`, `syscalls_rate`, `threads`, `mach_msgs`, `mach_msgs_rate` (7 MetricValue)

**Efficiency:** `instructions`, `cycles`, `ipc` (3 MetricValue)

**Power:** `energy`, `energy_rate`, `wakeups`, `wakeups_rate` (4 MetricValue)

**Contention:** `runnable_time`, `runnable_time_rate`, `qos_interactive`, `qos_interactive_rate` (4 MetricValue)

**State:** `state` (MetricValueStr), `priority` (MetricValue)

**Scoring:** `score`, `blocking_score`, `contention_score`, `pressure_score`, `efficiency_score` (5 MetricValue), `band` (MetricValueStr), `dominant_category` (str), `dominant_metrics` (list[str])

### Key Methods
| Method | Purpose |
|--------|---------|
| `collect()` | Async collection returning ProcessSamples |
| `_collect_sync()` | Synchronous collection loop over all PIDs |
| `_select_rogues()` | Select top-N processes by category thresholds |
| `_score_process()` | Compute weighted stress score with state multiplier |
| `_normalize_state()` | Convert state name to 0-1 severity |
| `_get_band()` | Map score to band name (low/medium/elevated/high/critical) |

### Scoring Algorithm (4-Category System)

The scoring system uses 4 categories to identify different types of process stress:

```
final_score = blocking × 0.40 + contention × 0.30 + pressure × 0.20 + efficiency × 0.10
```

**Blocking Score (40% of final) — Causes pauses:**
```
if state == "stuck": 100.0
else:
  pageins_rate / 100 × 35 +
  disk_io_rate / 100M × 35 +
  faults_rate / 10k × 30
```

**Contention Score (30% of final) — Fighting for resources:**
```
runnable_time_rate / 100 × 30 +
csw_rate / 10k × 30 +
cpu / 100 × 25 +
qos_interactive_rate / 100 × 15
```

**Pressure Score (20% of final) — Stressing system:**
```
mem / 8GB × 35 +
wakeups_rate / 1k × 25 +
syscalls_rate / 100k × 20 +
mach_msgs_rate / 10k × 20
```

**Efficiency Score (10% of final) — Wasting resources:**
```
ipc_penalty × has_cycles × 60 +
threads / 100 × 40

where ipc_penalty = max(0, 1 - ipc/0.5) if ipc < 0.5 else 0
```

**Normalization Thresholds (rate-based):**
| Metric | Threshold | Unit |
|--------|-----------|------|
| cpu | 100 | % |
| mem | 8 | GB |
| disk_io_rate | 100,000,000 | bytes/sec |
| pageins_rate | 100 | page-ins/sec |
| faults_rate | 10,000 | faults/sec |
| csw_rate | 10,000 | switches/sec |
| syscalls_rate | 100,000 | syscalls/sec |
| mach_msgs_rate | 10,000 | msgs/sec |
| wakeups_rate | 1,000 | wakeups/sec |
| runnable_time_rate | 100 | ms/sec (10% contention) |
| qos_interactive_rate | 100 | ms/sec |
| threads | 100 | count |
| ipc_min | 0.5 | IPC (below is penalty) |

**State Multipliers (post-score):**
| State | Multiplier |
|-------|------------|
| idle | 0.5 |
| sleeping | 0.5 |
| stopped | 0.7 |
| halted | 0.8 |
| zombie | 0.9 |
| running | 1.0 |
| stuck | 1.0 |

**Dominant Category:** The category with the highest score becomes `dominant_category`, and the top metrics in that category become `dominant_metrics` (formatted as "metric:value/s").

---

## Module: daemon.py

**Purpose:** Background sampler orchestrating collection, tracking, and broadcasting.

### Key Types
| Type | Purpose |
|------|---------|
| `DaemonState` | Runtime state (running, sample_count, current_score) |
| `Daemon` | Main daemon class |

### DaemonState Fields
```python
@dataclass
class DaemonState:
    running: bool = False
    sample_count: int = 0
    event_count: int = 0
    last_sample_time: datetime | None = None
    current_score: int = 0
```

### QoS Priority

The daemon sets `QOS_CLASS_USER_INITIATED` via `pthread_set_qos_class_self_np` at startup. This:
- Elevates CPU scheduling priority
- Improves I/O priority
- Reduces timer coalescing (more timely wakeups)

Unlike `nice -10`, QoS doesn't require root — it's a scheduler hint. Falls back to default priority if QoS fails.

### Daemon Attributes
| Attribute | Purpose |
|-----------|---------|
| `config` | Config instance |
| `collector` | LibprocCollector |
| `ring_buffer` | RingBuffer for recent samples |
| `tracker` | ProcessTracker for event lifecycle |
| `state` | DaemonState |
| `_socket_server` | SocketServer for TUI |
| `_conn` | SQLite connection |
| `_shutdown_event` | asyncio.Event for graceful shutdown |
| `_caffeinate_proc` | caffeinate subprocess (prevents sleep) |

### Key Methods
| Method | Purpose |
|--------|---------|
| `start()` | Initialize and run daemon |
| `stop()` | Graceful shutdown |
| `_main_loop()` | Core sampling loop |
| `_init_database()` | Initialize SQLite with schema |
| `_auto_prune()` | Daily pruning of old data |
| `_compute_pid_low_high()` | Enrich samples with historical ranges |
| `_forensics_callback()` | Callback for tracker-triggered forensics |
| `_ensure_tailspin_enabled()` | Enable tailspin on daemon start |
| `_disable_tailspin()` | Disable tailspin on daemon stop |

### Main Loop Flow
```
while not shutdown:
    1. samples = collector.collect()
    2. ring_buffer.push(samples)
    3. samples = _compute_pid_low_high(samples)  # Enrich with low/high
    4. ring_buffer.update_latest(samples)
    5. tracker.update(samples.rogues)  # Opens/closes events, triggers forensics
    6. socket_server.broadcast(samples)
    7. state.update_sample(samples.max_score)
    8. Sleep remaining interval (default ~0.333s = 3Hz)
```

### Tailspin Lifecycle

The daemon manages tailspin's lifecycle:
- `_ensure_tailspin_enabled()` runs `tailspin enable` at startup
- `_disable_tailspin()` runs `tailspin disable` at shutdown
- This ensures continuous kernel tracing while daemon runs

### Logging
Daemon uses structlog with dual output:
- **Console:** Human-readable format for development
- **File:** JSON Lines format at `~/.local/state/rogue-hunter/daemon.log`
  - Rotating: 5MB max, 3 backup files
  - Source field: `"source": "daemon"` or `"source": "tui"` (forwarded via socket)

---

## Module: tracker.py

**Purpose:** Per-process event lifecycle management with database persistence.

### Key Types
| Type | Purpose |
|------|---------|
| `TrackedProcess` | In-memory state for tracked process |
| `ProcessTracker` | Manages event lifecycle |

### Constants
| Constant | Purpose |
|----------|---------|
| `SNAPSHOT_ENTRY` | Snapshot type when entering bad band |
| `SNAPSHOT_EXIT` | Snapshot type when exiting bad band |
| `SNAPSHOT_CHECKPOINT` | Periodic snapshot while in bad band |

### TrackedProcess Fields
```python
@dataclass
class TrackedProcess:
    event_id: int           # Database row ID
    pid: int
    command: str
    peak_score: int         # Highest score seen
    peak_snapshot_id: int   # DB ID of peak snapshot
    last_checkpoint: float = 0.0  # Timestamp of last checkpoint
```

### ProcessTracker Attributes
| Attribute | Purpose |
|-----------|---------|
| `conn` | SQLite connection |
| `bands` | BandsConfig |
| `boot_time` | System boot timestamp |
| `tracked` | dict[int, TrackedProcess] (pid -> state) |
| `forensics_callback` | Async callback for forensics capture |

### Key Methods
| Method | Purpose |
|--------|---------|
| `_restore_open_events()` | Restore state from DB on daemon restart |
| `update(scores)` | Main update method (called each sample) |
| `_open_event(score)` | Create event when process enters bad band |
| `_close_event(pid, exit_time, exit_score)` | Close event when process exits |
| `_update_peak(score)` | Update peak when score increases |
| `_insert_checkpoint(score, tracked)` | Periodic checkpoint while in bad band |

### Tracking Rules
1. Process enters tracking when `score >= tracking_threshold` (default: 40 = elevated)
2. Entry snapshot saved with full ProcessScore
3. Checkpoints every `checkpoint_interval` seconds (default: 30)
4. Peak updated when score exceeds previous peak
5. Exit when score drops below threshold OR process disappears
6. Forensics triggered when process enters high band (score >= 50)

---

## Module: storage.py

**Purpose:** SQLite operations with WAL mode and auto-migration.

### Constants
| Constant | Value | Purpose |
|----------|-------|---------|
| `SCHEMA_VERSION` | 14 | Current schema version |

### Tables

**daemon_state** - Key-value store:
```sql
key TEXT PRIMARY KEY, value TEXT, updated_at REAL
```

**process_events** - One row per tracking event:
```sql
id INTEGER PRIMARY KEY, pid, command, boot_time,
entry_time, exit_time (NULL if open),
entry_band, peak_band, peak_score, peak_snapshot_id
```

**process_snapshots** - Full ProcessScore at entry/exit/checkpoint (108 columns in v14):
```sql
id, event_id (FK), snapshot_type, captured_at,
-- Each MetricValue field has current/low/high columns:
cpu, cpu_low, cpu_high,
mem, mem_low, mem_high, mem_peak,
pageins, pageins_low, pageins_high,
pageins_rate, pageins_rate_low, pageins_rate_high,
faults, faults_low, faults_high,
faults_rate, faults_rate_low, faults_rate_high,
-- ... (all metrics including new rate fields) ...
-- Contention section (new in v14):
runnable_time, runnable_time_low, runnable_time_high,
runnable_time_rate, runnable_time_rate_low, runnable_time_rate_high,
qos_interactive, qos_interactive_low, qos_interactive_high,
qos_interactive_rate, qos_interactive_rate_low, qos_interactive_rate_high,
-- Scoring section (expanded in v14):
score, score_low, score_high,
band, band_low, band_high,
blocking_score, blocking_score_low, blocking_score_high,
contention_score, contention_score_low, contention_score_high,
pressure_score, pressure_score_low, pressure_score_high,
efficiency_score, efficiency_score_low, efficiency_score_high,
dominant_category TEXT,
dominant_metrics TEXT (JSON array)
```

**forensic_captures** - Forensics capture metadata:
```sql
id, event_id (FK), captured_at, trigger,
spindump_status, tailspin_status, logs_status
```

**spindump_processes** - Parsed spindump process data:
```sql
id, capture_id (FK), pid, name, path,
parent_pid, parent_name, footprint_mb,
cpu_time_sec, thread_count
```

**spindump_threads** - Thread states from spindump:
```sql
id, process_id (FK), thread_id, thread_name,
sample_count, priority, cpu_time_sec, state, blocked_on
```

**log_entries** - System log entries:
```sql
id, capture_id (FK), timestamp, mach_timestamp,
subsystem, category, process_name, process_id,
message_type, event_message
```

**buffer_context** - Ring buffer state at capture:
```sql
id, capture_id (FK), sample_count, peak_score, culprits (JSON)
```

### Key Functions
| Function | Purpose |
|----------|---------|
| `init_database(path)` | Create/migrate database |
| `get_connection(path)` | Get connection with WAL mode |
| `create_process_event()` | Create new tracking event |
| `close_process_event()` | Set exit_time on event |
| `insert_process_snapshot()` | Store ProcessScore snapshot |
| `get_open_events()` | Get events with NULL exit_time |
| `prune_old_data(days)` | Delete events older than N days |

---

## Module: config.py

**Purpose:** TOML configuration loading/saving with dataclass schema.

### Key Types
| Type | Purpose |
|------|---------|
| `RetentionConfig` | Data retention settings |
| `SystemConfig` | Ring buffer size, sample interval |
| `BandsConfig` | Band thresholds and behavior |
| `ScoringWeights` | Per-metric scoring weights |
| `StateMultipliers` | Post-score state discounts |
| `NormalizationConfig` | Max values for normalization |
| `ScoringConfig` | Weights + multipliers + normalization |
| `CategorySelection` | Per-category rogue selection config |
| `StateSelection` | State-based rogue selection config |
| `RogueSelectionConfig` | Category selection rules |
| `Config` | Main container |

### SystemConfig Defaults
| Field | Default | Purpose |
|-------|---------|---------|
| `ring_buffer_size` | 60 | Samples in ring buffer |
| `sample_interval` | 1/3 (~0.333s) | Seconds between samples (3Hz) |
| `forensics_debounce` | 2.0 | Min seconds between forensics captures |

### BandsConfig Defaults
| Band | Threshold |
|------|-----------|
| low | 0 |
| medium | 20 |
| elevated | 40 |
| high | 50 |
| critical | 70 |
| tracking_band | "elevated" (40) |
| forensics_band | "high" (50) |
| checkpoint_interval | 30 seconds |

### Config Paths
| Property | Path |
|----------|------|
| `config_dir` | `~/.config/rogue-hunter/` |
| `config_path` | `~/.config/rogue-hunter/config.toml` |
| `data_dir` | `~/.local/share/rogue-hunter/` |
| `state_dir` | `~/.local/state/rogue-hunter/` |
| `db_path` | `~/.local/share/rogue-hunter/data.db` |
| `log_path` | `~/.local/state/rogue-hunter/daemon.log` |
| `pid_path` | `/tmp/rogue-hunter/daemon.pid` |
| `socket_path` | `/tmp/rogue-hunter/daemon.sock` |

---

## Module: ringbuffer.py

**Purpose:** Fixed-size circular buffer for recent samples (historical context).

### Key Types
| Type | Purpose |
|------|---------|
| `RingSample` | ProcessSamples wrapper with index |
| `BufferContents` | Immutable snapshot for forensics |
| `RingBuffer` | deque-based circular buffer |

### RingBuffer Methods
| Method | Purpose |
|--------|---------|
| `push(samples)` | Add new sample |
| `update_latest(samples)` | Replace most recent (after enrichment) |
| `freeze()` | Return immutable BufferContents |
| `clear()` | Empty buffer |
| `capacity` | Max size (default: 60 = ~20s at 3Hz) |
| `samples` | List of ProcessSamples |

**Note:** Ring buffer always contains data (top N rogues by score). This ensures forensic context is available when incidents occur.

---

## Module: socket_server.py / socket_client.py

**Purpose:** Bidirectional daemon-TUI communication via Unix socket.

### Protocol
Newline-delimited JSON over Unix socket at `/tmp/rogue-hunter/daemon.sock`

**Bidirectional:** Socket supports messages in both directions:
- Daemon → TUI: sample broadcasts, initial state
- TUI → Daemon: log messages (type: "log")

### Message Types
| Type | Direction | Contents |
|------|-----------|----------|
| `initial_state` | Daemon → TUI | Ring buffer state on connect |
| `sample` (default) | Daemon → TUI | ProcessSamples per iteration |
| `log` | TUI → Daemon | Log message forwarded to daemon's log file |

### SocketServer Methods
| Method | Purpose |
|--------|---------|
| `start()` | Begin accepting connections |
| `stop()` | Close all clients, remove socket |
| `broadcast(samples)` | Send to all connected clients |
| `has_clients` | Property: any clients connected? |
| `_handle_client_message()` | Route incoming messages by type |
| `_handle_log_message()` | Forward TUI logs to structlog |

### SocketClient Methods
| Method | Purpose |
|--------|---------|
| `connect()` | Connect to daemon socket |
| `disconnect()` | Close connection |
| `read_message()` | Read next JSON message |
| `send_message(msg)` | Send JSON message to daemon |
| `connected` | Property: is connected? |

---

## Module: forensics.py

**Purpose:** Diagnostic capture (tailspin + logs) when process enters high band.

**Privilege Model:** Only `tailspin save` requires sudo. Decoding and log extraction are unprivileged. The sudoers rule is configured during `rogue-hunter install` and restricts writes to `/tmp/rogue-hunter/`.

### Key Types
| Type | Purpose |
|------|---------|
| `SpindumpThread` | Parsed thread from spindump |
| `SpindumpProcess` | Parsed process from spindump |
| `LogEntry` | Parsed log entry |
| `ForensicsCapture` | Orchestrates capture and storage |

### Module Constants
| Constant | Value | Purpose |
|----------|-------|---------|
| `TAILSPIN_DIR` | `/tmp/rogue-hunter` | Sudoers-allowed write location |

### ForensicsCapture Methods
| Method | Purpose |
|--------|---------|
| `capture_and_store()` | Full capture: raw -> parse -> DB -> cleanup |
| `_capture_tailspin()` | Run `sudo -n tailspin save` to TAILSPIN_DIR |
| `_capture_logs()` | Run log show --style ndjson |
| `_process_tailspin()` | Decode via spindump -i, store in DB |
| `_process_logs()` | Parse NDJSON, store in DB |
| `_store_buffer_context()` | Store ring buffer culprits |

### Helper Functions
| Function | Purpose |
|----------|---------|
| `parse_spindump()` | Parse spindump output into process/thread dataclasses |
| `parse_logs_ndjson()` | Parse log show NDJSON output |
| `identify_culprits()` | Identify top processes from ring buffer |

### Capture Flow
1. Create temp directory (for logs)
2. Run `sudo -n tailspin save -o /tmp/rogue-hunter/...` and `log show` in parallel
3. Decode tailspin via `spindump -i` (unprivileged)
4. Parse and store in database
5. Clean up temp directory (tailspin files in /tmp cleared on reboot)

### Debouncing
Forensics captures are debounced via `forensics_debounce` config (default: 2 seconds). This prevents rapid-fire captures when a process hovers around the threshold.

### Why No Live Spindump

Live spindump (`spindump -notarget`) shows process state *after* a pause ends. But during the pause, our daemon was frozen too — we can't observe it while it happens. Tailspin's kernel buffer captured what happened *during* the freeze, which is the valuable diagnostic data. Decoding tailspin via `spindump -i` gives us the same thread/callstack format without needing a separate privileged capture.

### Install Command and Sudoers

The `rogue-hunter install` command (requires sudo) sets up:
1. Sudoers rule at `/etc/sudoers.d/rogue-hunter` allowing only `tailspin save -o /tmp/rogue-hunter/*`
2. Enables tailspin via `tailspin enable`
3. Creates launchd plist for service management

---

## Module: cli.py

**Purpose:** Click-based CLI commands.

### Commands
| Command | Purpose |
|---------|---------|
| `daemon` | Run background sampler (foreground) |
| `tui` | Launch interactive dashboard |
| `status` | Quick health check |
| `events` | List/show process events |
| `history` | Show score history |
| `prune` | Manual data pruning |
| `config show/edit/reset` | Configuration management |
| `install/uninstall` | launchd service setup |

---

## Module: tui/app.py

**Purpose:** Real-time Textual dashboard (single screen, socket-only).

### Key Types
| Type | Purpose |
|------|---------|
| `HeaderBar` | Score gauge with Unicode sparkline |
| `ProcessTable` | Top rogues with metrics |
| `DisplayTrackedProcess` | Tracked event for panel display |
| `TrackedEventsPanel` | Active tracking events |
| `ActivityLog` | Band transition log |
| `PauseMonitorApp` | Main App class |

### DisplayTrackedProcess Fields
```python
@dataclass
class DisplayTrackedProcess:
    command: str
    entry_time: float
    peak_score: int
    peak_categories: list[str] = field(default_factory=list)
    exit_time: float | None = None
    exit_reason: str = ""
```

### Layout
```
┌─────────────────────────────────────────┐
│ HeaderBar (4 lines)                     │
│   Score gauge + sparkline + status      │
├─────────────────────────────────────────┤
│ ProcessTable (1fr)                      │
│   Rogue processes with metrics          │
├────────────────────┬────────────────────┤
│ ActivityLog (12)   │ TrackedEventsPanel │
│ Band transitions   │ Active events      │
└────────────────────┴────────────────────┘
```

### Design Philosophy
- TUI = Real-time window into daemon state
- Display what daemon sends via socket
- **No database queries in TUI**
- CLI is for investigation; TUI is for "what's happening now"
- Single-screen dashboard (no page switching)

### PauseMonitorApp Methods
| Method | Purpose |
|--------|---------|
| `compose()` | Build widget layout |
| `on_mount()` | Start socket connection |
| `on_unmount()` | Cleanup on exit |
| `_try_socket_connect()` | Connect to daemon |
| `_initial_connect()` | First connection attempt + start reconnect on failure |
| `_reconnect_loop()` | Auto-reconnect with exponential backoff (1s→30s) |
| `_read_socket_loop()` | Continuous message reading |
| `_handle_socket_data()` | Update widgets from message |
| `_set_disconnected()` | Show disconnected state, trigger reconnect |

### Auto-Reconnect
TUI automatically reconnects when daemon restarts:
- Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s (capped)
- UI shows "reconnecting in Xs..." during backoff
- Reconnect loop cancelled on TUI exit

---

## Supporting Modules

### boottime.py
**Purpose:** Get system boot timestamp via sysctl.

| Function | Purpose |
|----------|---------|
| `get_boot_time()` | Returns boot timestamp as float |

### sleepwake.py
**Purpose:** Sleep/wake detection via pmset log parsing.

| Type | Purpose |
|------|---------|
| `SleepWakeType` | Enum: SLEEP, WAKE |
| `SleepWakeEvent` | Event with type, timestamp, reason |

| Function | Purpose |
|----------|---------|
| `parse_pmset_log()` | Parse pmset log output |
| `get_recent_sleep_events()` | Get recent sleep/wake events |
| `was_recently_asleep()` | Check if system was recently asleep |

### formatting.py
**Purpose:** Display formatting utilities.

| Function | Purpose |
|----------|---------|
| `format_duration()` | Format seconds as "1h 2m 3s" |
| `format_duration_verbose()` | Format with full words |
| `calculate_duration()` | Calculate duration between timestamps |

### sysctl.py
**Purpose:** ctypes bindings to sysctl.

| Function | Purpose |
|----------|---------|
| `sysctl_int()` | Read sysctl integer value |

---

## Design Decisions

| Decision | Why |
|----------|-----|
| libproc over top | Direct API: ~10-50ms vs ~2s, no subprocess overhead |
| Per-process scoring | Identify specific rogue processes, not just "system stressed" |
| 4-category scoring | Blocking/Contention/Pressure/Efficiency captures different rogue behaviors |
| Always show top N | TUI always has data; threshold only affects persistence |
| Score ALL, then select | All 500 processes scored, top N selected for display |
| Separate display vs tracking | Collector shows top rogues; ProcessTracker decides what to persist |
| MetricValue (current/low/high) | Show volatility and trends in single view |
| Ring buffer enrichment | Compute low/high from recent history without DB queries |
| Socket for TUI | Real-time streaming, no polling, minimal latency |
| No DB queries in TUI | Separation of concerns: daemon owns data, TUI displays |
| ProcessScore as canonical | Single source of truth for process data schema |
| Band-based thresholds | Configurable severity levels for different rogue behaviors |
| Forensics on band entry | Capture diagnostic data when rogues emerge |
| WAL mode SQLite | Safe concurrent access, crash recovery |
| Tailspin over live spindump | Captures kernel trace during incident, not after |
| Forensics debouncing | Prevents capture storms at threshold boundaries |
| Daemon manages tailspin lifecycle | Enable on start, disable on stop |
| Bidirectional socket | TUI can send logs to daemon's unified log file |

---

## Testing

| File | Coverage |
|------|----------|
| test_libproc.py | libproc.dylib bindings |
| test_collector.py | LibprocCollector, scoring, rogue selection |
| test_daemon.py | Daemon state, main loop |
| test_tracker.py | ProcessTracker event lifecycle |
| test_storage.py | Database operations, migrations |
| test_ringbuffer.py | Ring buffer operations |
| test_socket_server.py | Server start/stop/broadcast |
| test_socket_client.py | Client connect/read |
| test_tui_connection.py | TUI socket integration |
| test_tui_reconnect.py | TUI auto-reconnect behavior |
| test_forensics.py | Forensics capture, parsing |
| test_boottime.py | Boot time detection |
| test_config.py | Config loading/saving |
| test_formatting.py | Display formatting utilities |
| test_sleepwake.py | Sleep/wake detection |
| test_sysctl.py | sysctl bindings |
| test_tui.py | TUI widgets |
| test_cli.py | CLI commands |
| test_integration.py | End-to-end tests |
| test_no_tiers.py | Regression: no tier system |
| test_logging.py | Logging configuration |

Run: `uv run pytest`
