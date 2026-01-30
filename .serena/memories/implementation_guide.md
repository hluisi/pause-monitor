# Implementation Guide

**Last updated:** 2026-01-30
**Schema version:** 13

## Architecture

```
                              pause-monitor Architecture
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
│  │   • spindump                │    │
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

**Memory:** `mem`, `mem_peak`, `pageins`, `faults` (MetricValue + int)

**Disk I/O:** `disk_io`, `disk_io_rate` (MetricValue)

**Activity:** `csw`, `syscalls`, `threads`, `mach_msgs` (MetricValue)

**Efficiency:** `instructions`, `cycles`, `ipc` (MetricValue)

**Power:** `energy`, `energy_rate`, `wakeups` (MetricValue)

**State:** `state` (MetricValueStr), `priority` (MetricValue)

**Scoring:** `score` (MetricValue), `band` (MetricValueStr), `categories` (list[str])

### Key Methods
| Method | Purpose |
|--------|---------|
| `collect()` | Async collection returning ProcessSamples |
| `_collect_sync()` | Synchronous collection loop over all PIDs |
| `_select_rogues()` | Select top-N processes by category thresholds |
| `_score_process()` | Compute weighted stress score with state multiplier |
| `_normalize_state()` | Convert state name to 0-1 severity |
| `_get_band()` | Map score to band name (low/medium/elevated/high/critical) |

### Scoring Algorithm
```
1. Normalize each metric to 0-1 scale using config maximums
2. Weighted sum: base_score = sum(normalized[i] * weight[i])
3. Apply state multiplier (discount inactive processes)
4. Apply category bonus: 1.0 + 0.1 * max(0, category_count - 2)
5. Cap at 100
```

**Factors and Default Weights:**
| Factor | Weight | Normalization |
|--------|--------|---------------|
| cpu | 25 | cpu / 100 |
| state | 20 | stuck=1.0, zombie=0.8, etc. |
| pageins | 15 | pageins / 1000 |
| mem | 15 | mem / 8GB |
| csw | 10 | csw / 100k |
| syscalls | 5 | syscalls / 100k |
| threads | 0 | threads / 100 |
| disk_io_rate | 0 | rate / 100MB/s |
| energy_rate | 0 | rate / 100mW |
| wakeups | 0 | wakeups / 1000 |
| ipc | 0 | inverse (low IPC = stalled) |

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
|-----------|---------
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
    8. Sleep remaining interval (default 0.2s = 5Hz)
```

### Logging
Daemon uses structlog with dual output:
- **Console:** Human-readable format for development
- **File:** JSON Lines format at `~/.local/state/pause-monitor/daemon.log`
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
    last_checkpoint: float  # Timestamp of last checkpoint
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
6. Forensics triggered when process enters high band (score >= 80)

---

## Module: storage.py

**Purpose:** SQLite operations with WAL mode and auto-migration.

### Constants
| Constant | Value | Purpose |
|----------|-------|---------|
| `SCHEMA_VERSION` | 13 | Current schema version |

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

**process_snapshots** - Full ProcessScore at entry/exit/checkpoint:
```sql
id, event_id (FK), snapshot_type, captured_at,
-- Each MetricValue field has current/low/high columns:
cpu, cpu_low, cpu_high,
mem, mem_low, mem_high, mem_peak,
pageins, pageins_low, pageins_high,
... (all metrics)
score, score_low, score_high,
band, band_low, band_high,
categories (TEXT JSON)
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
| `RogueSelectionConfig` | Category selection rules |
| `Config` | Main container |

### BandsConfig Defaults
| Band | Threshold |
|------|-----------|
| low | 20 |
| medium | 40 |
| elevated | 60 |
| high | 80 |
| critical | 100 |
| tracking_band | "elevated" (40) |
| forensics_band | "high" (80) |
| checkpoint_interval | 30 seconds |

### Config Paths
| Property | Path |
|----------|------|
| `config_dir` | `~/.config/pause-monitor/` |
| `config_path` | `~/.config/pause-monitor/config.toml` |
| `data_dir` | `~/.local/share/pause-monitor/` |
| `state_dir` | `~/.local/state/pause-monitor/` |
| `db_path` | `~/.local/share/pause-monitor/data.db` |
| `log_path` | `~/.local/state/pause-monitor/daemon.log` |
| `pid_path` | `/tmp/pause-monitor/daemon.pid` |
| `socket_path` | `/tmp/pause-monitor/daemon.sock` |

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
| `capacity` | Max size (default: 150 = 30s at 5Hz) |
| `samples` | List of ProcessSamples |

---

## Module: socket_server.py / socket_client.py

**Purpose:** Bidirectional daemon-TUI communication via Unix socket.

### Protocol
Newline-delimited JSON over Unix socket at `/tmp/pause-monitor/daemon.sock`

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

**Privilege Model:** Only `tailspin save` requires sudo. Decoding and log extraction are unprivileged. The sudoers rule is configured during `pause-monitor install` and restricts writes to `/tmp/pause-monitor/`.

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
| `TAILSPIN_DIR` | `/tmp/pause-monitor` | Sudoers-allowed write location |

### ForensicsCapture Methods
| Method | Purpose |
|--------|---------|
| `capture_and_store()` | Full capture: raw -> parse -> DB -> cleanup |
| `_capture_tailspin()` | Run `sudo -n tailspin save` to TAILSPIN_DIR |
| `_capture_logs()` | Run log show --style ndjson |
| `_process_tailspin()` | Decode via spindump -i, store in DB |
| `_process_logs()` | Parse NDJSON, store in DB |
| `_store_buffer_context()` | Store ring buffer culprits |

### Capture Flow
1. Create temp directory (for logs)
2. Run `sudo -n tailspin save -o /tmp/pause-monitor/...` and `log show` in parallel
3. Decode tailspin via `spindump -i` (unprivileged)
4. Parse and store in database
5. Clean up temp directory (tailspin files in /tmp cleared on reboot)

### Why No Live Spindump

Live spindump (`spindump -notarget`) shows process state *after* a pause ends. But during the pause, our daemon was frozen too — we can't observe it while it happens. Tailspin's kernel buffer captured what happened *during* the freeze, which is the valuable diagnostic data. Decoding tailspin via `spindump -i` gives us the same thread/callstack format without needing a separate privileged capture.

### Install Command and Sudoers

The `pause-monitor install` command (requires sudo) sets up:
1. Sudoers rule at `/etc/sudoers.d/pause-monitor` allowing only `tailspin save -o /tmp/pause-monitor/*`
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
| `DisplayTrackedProcess` | Tracked event for panel |
| `TrackedEventsPanel` | Active tracking events |
| `ActivityLog` | Band transition log |
| `PauseMonitorApp` | Main App class |

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

## Design Decisions

| Decision | Why |
|----------|-----|
| libproc over top | Direct API: ~10-50ms vs ~2s, no subprocess overhead |
| Per-process tracking | Identify specific culprits, not just system stress |
| MetricValue (current/low/high) | Show volatility and trends in single view |
| Ring buffer enrichment | Compute low/high from recent history without DB queries |
| Socket for TUI | Real-time streaming, no polling, minimal latency |
| No DB queries in TUI | Separation of concerns: daemon owns data, TUI displays |
| ProcessScore as canonical | Single source of truth for process data schema |
| Band-based thresholds | Configurable severity levels for different behaviors |
| Forensics on band entry | Capture diagnostic data when problems emerge |
| WAL mode SQLite | Safe concurrent access, crash recovery |

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

Run: `uv run pytest`
