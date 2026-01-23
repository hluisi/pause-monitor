# Implementation Guide

> **Phase 6 COMPLETE (2026-01-22).** Tier-based event storage; SCHEMA_VERSION=6.

**Last updated:** 2026-01-23 (Full audit: Phase 3 complete)

This document describes the actual implementation of pause-monitor, module by module. For the canonical design specification, see `design_spec`. For known gaps, see `unimplemented_features`.

## Architecture Overview

```
                              +-----------------+
                              |   CLI (cli.py)  |
                              +--------+--------+
                                       |
          +----------------------------+-----------------------------+
          |                            |                             |
          v                            v                             v
+------------------+        +-------------------+         +------------------+
| daemon command   |        |  status/events/   |         |  tui command     |
|                  |        |  history/prune    |         |                  |
+--------+---------+        +--------+----------+         +--------+---------+
         |                           |                             |
         v                           |                             v
+------------------+                 |                   +------------------+
|     Daemon       |<--------------->+------------------>|  PauseMonitorApp |
|  (daemon.py)     |                 |                   |   (tui/app.py)   |
+--------+---------+                 |                   +--------+---------+
         |                           |                             |
         |  powermetrics             |  SQLite                     |  Unix socket
         |  stream                   |  (events only)              |  (real-time)
         v                           v                             v
+------------------+        +------------------+          +------------------+
| PowermetricsStream        |     Storage      |          |   SocketClient   |
|  (collector.py)  |        |   (storage.py)   |          | (socket_client)  |
+--------+---------+        +--------+---------+          +--------+---------+
         |                           ^                             ^
         |                           |                             |
         v                           |                             |
+------------------+                 |                   +------------------+
|   TierManager    |-----------------+------------------>|   SocketServer   |
|  (sentinel.py)   |                                     | (socket_server)  |
+--------+---------+                                     +------------------+
         |
         v
+------------------+        +------------------+          +------------------+
|   RingBuffer     |        |   Forensics      |          |   Notifications  |
| (ringbuffer.py)  |------->|  (forensics.py)  |          | (notifications)  |
+------------------+        +------------------+          +------------------+
```

## Architecture (Post-Redesign)

### Data Flow
1. **Single 10Hz loop** driven by powermetrics stream (100ms interval)
2. Each sample: powermetrics → `_calculate_stress()` → ring buffer → TierManager → SocketServer
3. **Ring buffer** receives ALL samples continuously (no SQLite for normal samples)
4. **TUI** streams from Unix socket (real-time, not SQLite polling)
5. **SQLite** stores only tier events (escalation episodes with their samples)

### Key Components
- `Daemon._main_loop()` - Main 10Hz processing loop
- `Daemon._calculate_stress()` - 8-factor stress from powermetrics
- `TierManager` - Tier state machine (extracted from deleted Sentinel class)
- `SocketServer` - Push-based broadcast to TUI clients
- `SocketClient` - TUI receives real-time data

### Deleted (No Longer Exists)
- `Sentinel` class - Deleted entirely in Phase 5; use `TierManager` directly
- `calculate_stress()` function - Deleted; use `Daemon._calculate_stress()`
- `IOBaselineManager` class - Deleted; I/O stress from powermetrics directly
- `SamplePolicy` - Never existed
- `slow_interval_ms` config - Never existed

---

## Module: cli.py

**Purpose:** Click-based CLI entry point with commands for daemon, TUI, status, events, history, config, and service management.

### Functions
| Function | Purpose |
|----------|---------|
| `main()` | Click group entry point with version option |
| `daemon()` | Run background sampler via `asyncio.run(run_daemon())` |
| `tui()` | Launch interactive Textual dashboard |
| `status()` | Quick health check - daemon status, last sample, recent events |
| `events()` | Group for event commands; lists events with `--limit`/`--status` filtering |
| `events_show(event_id)` | Display full event details with samples and top processes |
| `events_mark(event_id)` | Change event status (reviewed/pinned/dismissed) or add notes |
| `history(hours, fmt)` | Query historical samples with table/json/csv output |
| `prune(events_days, dry_run, force)` | Delete old reviewed/dismissed events |
| `config()` | Group for config subcommands |
| `config_show()` | Display current configuration |
| `config_edit()` | Open config.toml in editor |
| `config_reset()` | Reset to defaults |
| `install(system_wide, force)` | Set up launchd service |
| `uninstall()` | Remove launchd service |

### Data Flow
- Commands load `Config`, open SQLite connection, call storage functions
- `daemon` command calls `run_daemon()` which creates `Daemon` instance
- `tui` command calls `run_tui(config)` which creates `PauseMonitorApp`

---

## Module: config.py

**Purpose:** Configuration loading/saving with TOML format and XDG-compliant paths.

### Classes
| Class | Purpose |
|-------|---------|
| `SamplingConfig` | Sampling intervals (normal=5s, elevated=1s) and thresholds |
| `RetentionConfig` | Data retention (samples_days=30, events_days=90) |
| `AlertsConfig` | Notification settings (pause_detected, critical_stress, sound, etc.) |
| `SuspectsConfig` | Known problematic process patterns for culprit identification |
| `SentinelConfig` | Fast loop timing (fast_interval_ms=100, ring_buffer_seconds=30, pause_threshold_ratio=2.0, peak_tracking_seconds=30) |
| `TiersConfig` | Tier thresholds (elevated=15, critical=50) |
| `Config` | Main container with all sub-configs and path methods |

### Config Properties
| Property | Path |
|----------|------|
| `config_dir` | `~/.config/pause-monitor/` |
| `config_path` | `~/.config/pause-monitor/config.toml` |
| `data_dir` | `~/.local/share/pause-monitor/` |
| `db_path` | `~/.local/share/pause-monitor/data.db` |
| `events_dir` | `~/.local/share/pause-monitor/events/` |
| `log_path` | `~/.local/share/pause-monitor/daemon.log` |
| `pid_path` | `~/.local/share/pause-monitor/daemon.pid` |
| `socket_path` | `~/.local/share/pause-monitor/daemon.sock` |

### Key Methods
- `save(path)` - Write to TOML with tomlkit
- `load(path)` - Load from TOML, defaults for missing values

---

## Module: daemon.py

**Purpose:** Main daemon orchestrating continuous monitoring, stress calculation, tier management, and pause detection.

### Classes

#### DaemonState
Runtime state dataclass tracking:
- `running`, `sample_count`, `event_count`
- `last_sample_time`, `current_stress`
- `elevated_since`, `critical_since` (for duration tracking)

Methods: `update_sample()`, `enter_elevated()`, `exit_elevated()`, `enter_critical()`, `exit_critical()`
Properties: `elevated_duration`, `critical_duration`

#### Daemon
Main orchestrator class. Key attributes:
- `config`, `state`, `notifier`, `core_count`
- `ring_buffer` - RingBuffer for 30s of samples
- `tier_manager` - TierManager for state machine
- `_current_event_id` - Active escalation event (tier 2+)
- `_socket_server` - SocketServer for TUI streaming
- `_powermetrics` - PowermetricsStream instance

### Key Methods (Daemon)
| Method | Purpose |
|--------|---------|
| `__init__(config)` | Wire up TierManager, RingBuffer, Notifier |
| `_init_database()` | Initialize DB (extracted for testing) |
| `start()` | Init DB, write PID, start caffeinate, start socket server, run main loop |
| `stop()` | Stop socket server, cancel tasks, cleanup |
| `_handle_signal(sig)` | Handle SIGTERM/SIGINT, terminate powermetrics |
| `_start_caffeinate()` | Prevent App Nap with `/usr/bin/caffeinate -i` |
| `_stop_caffeinate()` | Terminate caffeinate subprocess |
| `_write_pid_file()` | Write PID to `daemon.pid` |
| `_remove_pid_file()` | Remove PID file |
| `_check_already_running()` | Check if daemon already running via PID file |
| `_auto_prune()` | Daily auto-prune task |
| `_run_heavy_capture(capture)` | Run spindump/tailspin/logs in background |
| `_run_forensics(contents, duration)` | Full forensics on pause detection |
| `_handle_tier_change(action, tier)` | Update state and notify on tier transitions |
| `_save_event_sample(metrics, stress, tier)` | Save sample to current event (tier 2/3) |
| `_handle_tier_action(action, stress, metrics)` | Handle tier transitions, create/finalize events |
| `_maybe_update_peak(stress)` | Periodic peak tracking during elevated states |
| `_handle_pause(actual, expected)` | Handle detected pause - run forensics |
| `_calculate_stress(pm_result, latency_ratio)` | Calculate 8-factor StressBreakdown |
| `_main_loop()` | Main 10Hz loop: powermetrics → stress → buffer → tiers → broadcast |

### Stress Calculation (_calculate_stress)
8 factors computed from PowermetricsResult + system metrics:
- `load` (0-30): Load average / core count ratio
- `memory` (0-30): Memory used % from `kern.memorystatus_level`
- `thermal` (0-10): 10 if throttled, else 0
- `latency` (0-20): Actual/expected interval ratio
- `io` (0-10): Disk I/O MB/s from powermetrics
- `gpu` (0-20): GPU utilization % from powermetrics
- `wakeups` (0-10): Idle wakeups/s from powermetrics
- `pageins` (0-30): Swap pageins/s (CRITICAL for pause detection)

### Main Loop Flow
```python
async for pm_result in powermetrics.read_samples():
    stress = _calculate_stress(pm_result, latency_ratio)
    ring_buffer.push(pm_result, stress, tier)
    socket_server.broadcast(pm_result, stress, tier)
    action = tier_manager.update(stress.total)
    if action:
        _handle_tier_action(action, stress, metrics=pm_result)
    if tier >= 3:
        _save_event_sample(pm_result, stress, tier=3)
    if latency_ratio > threshold:
        _handle_pause(actual_interval, expected_interval)
```

---

## Module: collector.py

**Purpose:** Metrics collection via powermetrics subprocess streaming.

### Classes

#### StreamStatus
Enum: `NOT_STARTED`, `RUNNING`, `STOPPED`, `FAILED`

#### PowermetricsResult
Dataclass with fields from powermetrics plist:
- `elapsed_ns` - Actual sample interval
- `throttled` - Thermal throttling active
- `cpu_power`, `gpu_pct`, `gpu_power` - Power metrics
- `io_read_per_s`, `io_write_per_s` - Disk I/O
- `wakeups_per_s` - Idle wakeups (summed from tasks)
- `pageins_per_s` - Swap pageins (summed from tasks)
- `top_cpu_processes` - List[dict] with name, pid, cpu_ms_per_s
- `top_pagein_processes` - List[dict] with name, pid, pageins_per_s
- `top_wakeup_processes` - List[dict] with name, pid, wakeups_per_s
- `top_diskio_processes` - List[dict] with name, pid, diskio_per_s

#### PowermetricsStream
Async streaming reader for powermetrics plist output.

Command: `/usr/bin/powermetrics --samplers cpu_power,gpu_power,thermal,tasks,disk -f plist -i 100`

Methods:
- `start()` - Start subprocess, handle permission errors
- `stop()` - Terminate subprocess gracefully
- `terminate()` - SIGKILL for signal handlers
- `read_samples()` - AsyncIterator yielding PowermetricsResult

### Key Functions
| Function | Purpose |
|----------|---------|
| `get_core_count()` | Return `os.cpu_count()` |
| `parse_powermetrics_sample(data)` | Parse plist bytes → PowermetricsResult |

---

## Module: stress.py

**Purpose:** Stress breakdown dataclass and memory pressure utilities.

### Classes

#### MemoryPressureLevel
Enum: `NORMAL` (>50%), `WARNING` (20-50%), `CRITICAL` (<20%)
Class method: `from_percent(available_pct)`

#### StressBreakdown
Dataclass with 8 stress factors (canonical definition, imported by storage.py):
- `load` (0-30), `memory` (0-30), `thermal` (0-10), `latency` (0-20)
- `io` (0-10), `gpu` (0-20), `wakeups` (0-10), `pageins` (0-30)

Property: `total` - Sum capped at 100

### Functions
| Function | Purpose |
|----------|---------|
| `get_memory_pressure_fast()` | Get memory via `sysctl_int("kern.memorystatus_level")` |

---

## Module: storage.py

**Purpose:** SQLite database operations with tier-based event storage.

### Constants
- `SCHEMA_VERSION = 6` (tier-based saving redesign)
- `VALID_EVENT_STATUSES = {"unreviewed", "reviewed", "pinned", "dismissed"}`

### Schema (SCHEMA_VERSION=6)

**Primary tables (tier-based saving):**

`events` - One row per escalation episode (tier1 → elevated → tier1):
- `id`, `start_timestamp`, `end_timestamp` (NULL if ongoing)
- `peak_stress`, `peak_tier` (highest tier reached)
- `status` (unreviewed/reviewed/pinned/dismissed), `notes`

`event_samples` - Samples captured during events:
- `id`, `event_id` (FK), `timestamp`, `tier` (2=peak, 3=continuous)
- Metrics: `elapsed_ns`, `throttled`, `cpu_power`, `gpu_pct`, `gpu_power`
- I/O: `io_read_per_s`, `io_write_per_s`, `wakeups_per_s`, `pageins_per_s`
- Stress: `stress_total`, `stress_load`, ..., `stress_pageins`
- Top processes: `top_cpu_procs`, `top_pagein_procs`, `top_wakeup_procs`, `top_diskio_procs` (JSON)

**Legacy tables (kept for backward compatibility, not used):**
- `samples` - Individual metrics samples
- `process_samples` - Per-process data for samples
- `daemon_state` - Key-value state storage

### Dataclasses
| Class | Purpose |
|-------|---------|
| `Sample` | Single metrics sample (legacy format) |
| `Event` | Escalation event with timestamps, peak values, status |
| `EventSample` | Sample during escalation with full metrics |

### Functions
| Function | Purpose |
|----------|---------|
| `init_database(path)` | Create tables, enable WAL, set schema version |
| `get_connection(path)` | Return sqlite3.Connection |
| `get_schema_version(conn)` | Read schema version from daemon_state |
| `insert_sample(conn, sample)` | Insert legacy sample |
| `get_recent_samples(conn, limit)` | Get newest legacy samples |
| `create_event(conn, start_timestamp)` | Create new event, return ID |
| `finalize_event(conn, id, end, peak_stress, peak_tier)` | Finalize event on tier1 return |
| `insert_event_sample(conn, sample)` | Insert event sample |
| `get_events(conn, start, end, limit, status)` | Get events with filtering |
| `get_event_by_id(conn, id)` | Get single event |
| `get_event_samples(conn, event_id)` | Get all samples for event |
| `update_event_status(conn, id, status, notes)` | Update event status/notes |
| `prune_old_data(conn, events_days)` | Delete old reviewed/dismissed events |

---

## Module: sentinel.py

**Purpose:** Tier state machine for stress level transitions.

### Classes

#### Tier
IntEnum: `SENTINEL=1`, `ELEVATED=2`, `CRITICAL=3`

#### TierAction
StrEnum: `TIER2_ENTRY`, `TIER2_EXIT`, `TIER2_PEAK`, `TIER3_ENTRY`, `TIER3_EXIT`

#### TierManager
Manages tier transitions with hysteresis (5s delay for de-escalation).

State machine:
```
SENTINEL (tier 1) ─── stress >= elevated ──→ ELEVATED (tier 2)
    ^                                              │
    │                                              │ stress >= critical
    │                                              ↓
    └──── stress < elevated (5s) ──── ELEVATED ←── CRITICAL (tier 3)
                                           ↑          │
                                           └── stress < critical (5s)
```

Methods:
- `__init__(elevated_threshold, critical_threshold, deescalation_delay)`
- `current_tier` (property) - Returns 1, 2, or 3
- `peak_stress` (property) - Peak stress since elevation
- `tier2_entry_time`, `tier3_entry_time` (properties)
- `update(stress_total)` - Process stress, return TierAction or None

---

## Module: ringbuffer.py

**Purpose:** Fixed-size ring buffer for stress samples with process snapshot support.

### Classes

#### ProcessInfo
Dataclass: `pid`, `name`, `cpu_pct`, `memory_mb`

#### ProcessSnapshot
Dataclass: `timestamp`, `trigger`, `by_cpu` (list), `by_memory` (list)

#### RingSample
Dataclass: `timestamp`, `metrics` (PowermetricsResult), `stress` (StressBreakdown), `tier`

#### BufferContents
Dataclass (immutable): `samples` (list), `snapshots` (list)

#### RingBuffer
Main implementation using `collections.deque(maxlen=...)`.

Methods:
- `__init__(max_samples=300)` - 30s at 100ms
- `samples` (property) - Copy of samples list
- `snapshots` (property) - Copy of snapshots list
- `push(metrics, stress, tier)` - Add sample to buffer
- `snapshot_processes(trigger)` - Capture top 10 by CPU/memory via psutil
- `freeze()` - Return immutable BufferContents copy
- `clear_snapshots()` - Clear on de-escalation

---

## Module: socket_server.py

**Purpose:** Unix domain socket server for push-based streaming to TUI.

### SocketServer
Push-based design: main loop calls `broadcast()` after each sample.

Protocol: Newline-delimited JSON over Unix socket at `daemon.sock`

Message types:
- `initial_state` - Sent on connect with recent buffer samples
- `sample` - Sent via broadcast with current metrics/stress/tier

Methods:
- `__init__(socket_path, ring_buffer)`
- `has_clients` (property) - For main loop optimization
- `start()` - Create socket, chmod for non-root TUI access
- `stop()` - Close connections, remove socket file
- `broadcast(metrics, stress, tier, load_avg, mem_pressure)` - Push to all clients
- `_handle_client(reader, writer)` - Handle connection lifecycle
- `_send_initial_state(writer)` - Send ring buffer state on connect

---

## Module: socket_client.py

**Purpose:** Unix domain socket client for TUI to receive real-time data.

### SocketClient
Simple and stateless: connects or throws. TUI handles reconnection.

Methods:
- `__init__(socket_path)`
- `connected` (property) - Connection status
- `connect()` - Raises FileNotFoundError if socket missing
- `disconnect()` - Close connection
- `read_message()` - Read next JSON message, raises ConnectionError if lost

---

## Module: forensics.py

**Purpose:** Capture diagnostic data on pause detection.

### ForensicsCapture
Context for writing artifacts to event directory.

Methods:
- `__init__(event_dir)`
- `write_metadata(data)` - Write metadata.json
- `write_process_snapshot(processes)` - Write processes.json
- `write_text_artifact(name, content)` - Write text file
- `write_binary_artifact(name, data)` - Write binary file
- `write_ring_buffer(contents)` - Write ring_buffer.json with samples and snapshots

### Functions
| Function | Purpose |
|----------|---------|
| `create_event_dir(events_dir, event_time)` | Create timestamped directory |
| `identify_culprits(contents)` | Map stress factors to responsible processes |
| `capture_spindump(event_dir, timeout)` | Run `/usr/sbin/spindump` |
| `capture_tailspin(event_dir, timeout)` | Run `/usr/bin/tailspin save` |
| `capture_system_logs(event_dir, window, timeout)` | Run `/usr/bin/log show` |
| `run_full_capture(capture, window)` | Run all captures concurrently |

### Culprit Identification Logic
1. Find MAX stress for each factor across all buffer samples
2. Map factors to process sources:
   - `load`, `thermal`, `latency`, `gpu` → top CPU processes
   - `memory` → top memory processes (from snapshots)
   - `io` → top disk I/O processes (from metrics)
   - `wakeups` → top wakeup processes (from metrics)
   - `pageins` → top pagein processes (from metrics)
3. Return factors >= threshold (10) with their top 5 processes

---

## Module: notifications.py

**Purpose:** macOS notification center integration.

### NotificationType
Enum: `PAUSE_DETECTED`, `CRITICAL_STRESS`, `ELEVATED`, `FORENSICS_COMPLETED`

### Notifier
Manages notifications based on AlertsConfig.

Methods:
- `__init__(config: AlertsConfig)`
- `pause_detected(duration, event_dir)` - Notify of pause
- `critical_stress(stress_total, duration)` - Notify sustained critical
- `elevated_entered(stress_total)` - Notify entering elevated (if enabled)
- `forensics_completed(event_dir)` - Notify capture complete

### Functions
| Function | Purpose |
|----------|---------|
| `send_notification(title, message, sound)` | Send via osascript |

---

## Module: sleepwake.py

**Purpose:** Detect system sleep/wake to exclude false pause detections.

### Classes

#### SleepWakeType
Enum: `SLEEP`, `WAKE`, `DARK_WAKE`

#### SleepWakeEvent
Dataclass: `timestamp`, `event_type`, `reason`

#### PauseEvent
Dataclass: `timestamp`, `duration`, `expected`, `latency_ratio`

#### PauseDetector
Detect pauses via timing anomalies.

Methods:
- `__init__(expected_interval, pause_threshold=2.0)`
- `check(actual_interval, recent_wake)` - Return PauseEvent or None

### Functions
| Function | Purpose |
|----------|---------|
| `parse_pmset_log(output)` | Parse `pmset -g log` output |
| `get_recent_sleep_events(since)` | Get events from pmset log |
| `was_recently_asleep(within_seconds)` | Check if system just woke |

---

## Module: sysctl.py

**Purpose:** Direct sysctl access via ctypes for fast metrics.

### Functions
| Function | Purpose |
|----------|---------|
| `sysctl_int(name)` | Read integer sysctl by MIB name (e.g., "kern.memorystatus_level") |

Uses `libc.sysctlbyname()` directly (~20us vs ~10ms for subprocess).

---

## Module: tui/app.py

**Purpose:** Textual-based interactive dashboard with real-time socket streaming.

### Classes

#### StressGauge
Visual stress meter with color coding (green→yellow→red).

Methods: `update_stress(total)`, `set_disconnected()`

#### MetricsPanel
Display CPU power, load, memory, pageins, throttling.

Methods: `update_metrics(data)`, `set_disconnected()`

#### ProcessesPanel
Display top CPU and pagein processes from socket data.

Methods: `update_processes(cpu_processes, pagein_processes)`, `set_disconnected()`

#### EventsTable
DataTable showing recent events from database.

#### EventDetailScreen
Modal screen for viewing/editing single event.

#### EventsScreen
Full events list with filtering (unreviewed/all).

#### PauseMonitorApp
Main Textual App class.

Layout: 2x3 grid with stress gauge, metrics, breakdown, processes, events

Methods:
- `__init__(config)`
- `on_mount()` - Connect DB, attempt socket connection, start refresh timer
- `on_unmount()` - Cancel tasks, disconnect socket, close DB
- `_try_socket_connect()` - Async socket connection
- `_read_socket_loop()` - Read messages and update UI
- `_set_disconnected(error)` - Update UI for disconnected state
- `_handle_socket_data(data)` - Process socket messages
- `compose()` - Build UI layout
- `_refresh_events()` - Refresh events table from DB
- `action_refresh()`, `action_show_events()`, `action_show_history()`

Bindings: q=quit, r=refresh, e=events, h=history

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single 10Hz loop | Powermetrics stream drives timing; simpler than separate fast/slow loops |
| Unix socket for TUI | Real-time data without SQLite polling overhead |
| TierManager extracted from Sentinel | Reusable state machine; Sentinel class deleted entirely |
| Ring buffer for forensics | Capture pre-pause context without storage overhead |
| Tier hysteresis (5s delay) | Prevent oscillation between states |
| Streaming powermetrics | Lower latency than exec-per-sample approach |
| Event status flags | Allow user triage; protect important events from pruning |
| WAL mode SQLite | Better concurrent read/write performance |
| TOML config format | Human-readable, standard Python tooling |
| XDG paths | Standard macOS/Linux config/data locations |
| StressBreakdown as canonical type | Single source of truth, imported by storage.py |
| SQLite for tier events only | Normal samples flow through socket; only escalations persisted |
| Push-based socket broadcast | Main loop calls broadcast(), no internal polling |
| Pageins as 8th stress factor | Critical indicator for pause detection |

---

## Testing

### Test Files
| File | Coverage |
|------|----------|
| `tests/test_cli.py` | CLI commands |
| `tests/test_config.py` | Config loading/saving |
| `tests/test_daemon.py` | Daemon state, PID file, main loop |
| `tests/test_collector.py` | Powermetrics parsing |
| `tests/test_stress.py` | Stress calculation, memory pressure |
| `tests/test_storage.py` | Database operations, migrations |
| `tests/test_forensics.py` | Forensics capture |
| `tests/test_notifications.py` | Notification sending |
| `tests/test_sleepwake.py` | Sleep/wake detection, pmset parsing |
| `tests/test_tier_manager.py` | TierManager state machine |
| `tests/test_ringbuffer.py` | Ring buffer operations |
| `tests/test_socket_server.py` | Socket server broadcast |
| `tests/test_socket_client.py` | Socket client connection |
| `tests/test_sysctl.py` | Sysctl direct access |
| `tests/test_tui_connection.py` | TUI socket connection |
| `tests/test_integration.py` | End-to-end tests |
| `tests/conftest.py` | Shared fixtures |

### Running Tests
```bash
uv run pytest                    # All tests
uv run pytest -v                 # Verbose
uv run pytest tests/test_daemon.py  # Specific module
uv run pytest -k "tier"          # Pattern matching
```

### Shared Fixtures (conftest.py)
- `tmp_db` - Temporary database path
- `initialized_db` - Database with schema initialized
- `sample_stress` - StressBreakdown for testing
