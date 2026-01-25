# Implementation Guide

> **Phase 7 COMPLETE (2026-01-24).** Per-process scoring; SCHEMA_VERSION=7.

**Last updated:** 2026-01-24 (Full audit: Phase 3 complete)

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
         |  top command              |  SQLite                     |  Unix socket
         |  (1Hz)                    |  (events only)              |  (real-time)
         v                           v                             v
+------------------+        +------------------+          +------------------+
|   TopCollector   |        |     Storage      |          |   SocketClient   |
|  (collector.py)  |        |   (storage.py)   |          | (socket_client)  |
+--------+---------+        +--------+---------+          +--------+---------+
         |                           ^                             ^
         | ProcessSamples            |                             |
         | with rogues               |                             |
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
1. **Single 1Hz loop** driven by TopCollector (1-second intervals via `top -l 2`)
2. Each sample: top → parse → rogue selection → scoring → ring buffer → TierManager → SocketServer
3. **Ring buffer** receives ALL samples continuously (no SQLite for normal samples)
4. **TUI** streams from Unix socket (real-time, not SQLite polling)
5. **SQLite** stores only tier events (escalation episodes with their process samples as JSON blobs)

### Key Components
- `Daemon._main_loop()` - Main 1Hz processing loop
- `TopCollector.collect()` - Per-process scoring from top command
- `TierManager` - Tier state machine (thresholds based on max_score)
- `SocketServer` - Push-based broadcast to TUI clients
- `SocketClient` - TUI receives real-time data

### Deleted (No Longer Exists)
- `stress.py` - Deleted entirely; per-process scoring replaced global stress
- `StressBreakdown` - Deleted; use `ProcessScore.score` (per-process)
- `PowermetricsStream` - Deleted; use `TopCollector`
- `Sentinel` class - Deleted; use `TierManager` directly
- `calculate_stress()` function - Deleted
- `IOBaselineManager` class - Deleted
- `_calculate_stress()` method - Deleted from Daemon
- `_maybe_update_peak()` method - Deleted from Daemon

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
| `SentinelConfig` | Timing (fast_interval_ms=100, ring_buffer_seconds=30, pause_threshold_ratio=2.0, peak_tracking_seconds=30) |
| `TiersConfig` | Tier thresholds (elevated=35, critical=65) |
| `ScoringWeights` | Per-process scoring weights: cpu=25, state=20, pageins=15, mem=15, cmprs=10, csw=10, sysbsd=5, threads=0 |
| `ScoringConfig` | Container for scoring weights |
| `CategorySelection` | Per-category rogue selection config: enabled, count, threshold |
| `StateSelection` | State-based rogue selection: enabled, count, states=['zombie'] |
| `RogueSelectionConfig` | Rogue selection for all categories: cpu, mem, cmprs, threads, csw, sysbsd, pageins, state |
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

## Module: collector.py

**Purpose:** Process data collection via `top` command with per-process scoring.

### Classes

#### ProcessScore
Dataclass representing a single scored process:
- `pid`, `command`, `cpu`, `state`
- `mem`, `cmprs`, `pageins` - Memory metrics (bytes)
- `csw`, `sysbsd` - Context switches and syscalls
- `threads` - Thread count
- `score` - Weighted score (0-100)
- `categories` - frozenset of categories that selected this process

Methods: `to_dict()`, `from_dict()` for serialization

#### ProcessSamples
Collection of scored processes from one sample:
- `timestamp`, `elapsed_ms`, `process_count`
- `max_score` - Highest score among rogues
- `rogues` - list[ProcessScore] of selected "rogue" processes

Methods: `to_json()`, `from_json()` for serialization

#### TopCollector
Collects process data via `top` at 1Hz.

Command: `top -l 2 -s 1 -stats pid,command,cpu,state,mem,cmprs,threads,csw,sysbsd,pageins`

Methods:
- `__init__(config)` - Store config for scoring weights
- `_parse_memory(value)` - Parse "339M", "1G" etc to bytes
- `_parse_top_output(raw)` - Parse top output to process dicts
- `_select_rogues(processes)` - Apply rogue selection rules from config
- `_normalize_state(state)` - Map state to 0-1 score (stuck=1.0, zombie=0.8, etc.)
- `_score_process(proc)` - Compute weighted score using config weights
- `collect()` - Run top, parse, select rogues, score, return ProcessSamples
- `_run_top()` - Execute top command with timeout

### Rogue Selection Logic
1. **Always include stuck** (hardcoded, not configurable)
2. **Include configured states** (zombie by default) via `state.states` config
3. **Top N per enabled category** above threshold:
   - cpu, mem, cmprs, threads, csw, sysbsd, pageins
   - Each has `enabled`, `count`, `threshold` settings

### Scoring Formula
8 factors with configurable weights (default sum = 100):
- `cpu` (25): CPU% / 100
- `state` (20): stuck=1.0, zombie=0.8, halted=0.6, stopped=0.4, else=0.0
- `pageins` (15): pageins / 1000
- `mem` (15): mem / 8GB
- `cmprs` (10): compressed memory / 1GB
- `csw` (10): context switches / 100k
- `sysbsd` (5): syscalls / 100k
- `threads` (0): threads / 1000 (disabled by default)

Score = sum(normalized[i] * weight[i]), capped at 100

### Functions
| Function | Purpose |
|----------|---------|
| `get_core_count()` | Return `os.cpu_count()` |

---

## Module: storage.py

**Purpose:** SQLite database operations with tier-based event storage.

### Constants
- `SCHEMA_VERSION = 7` (JSON blob storage for process samples)
- `VALID_EVENT_STATUSES = {"unreviewed", "reviewed", "pinned", "dismissed"}`

### Schema (SCHEMA_VERSION=7)

**Primary tables (tier-based saving with JSON blobs):**

`events` - One row per escalation episode (tier1 -> elevated -> tier1):
- `id`, `start_timestamp`, `end_timestamp` (NULL if ongoing)
- `peak_stress` (stores max_score), `peak_tier` (highest tier reached)
- `status` (unreviewed/reviewed/pinned/dismissed), `notes`

`process_sample_records` - Process samples as JSON blobs (new in v7):
- `id`, `event_id` (FK), `tier` (2=peak, 3=continuous)
- `data` (JSON blob containing serialized ProcessSamples)

**Legacy tables (kept for backward compatibility, event_samples not used):**
- `event_samples` - Individual metrics samples (pre-v7)
- `samples` - Legacy individual metrics samples
- `daemon_state` - Key-value state storage

### Dataclasses
| Class | Purpose |
|-------|---------|
| `Event` | Escalation event with timestamps, peak_stress (max_score), peak_tier, status |
| `ProcessSampleRecord` | Process sample record with event_id, tier, data (ProcessSamples) |

### Functions
| Function | Purpose |
|----------|---------|
| `init_database(path)` | Create tables, enable WAL, set schema version |
| `get_connection(path)` | Return sqlite3.Connection |
| `get_schema_version(conn)` | Read schema version from daemon_state |
| `create_event(conn, start_timestamp)` | Create new event, return ID |
| `finalize_event(conn, id, end, peak_stress, peak_tier)` | Finalize event on tier1 return |
| `get_events(conn, start, end, limit, status)` | Get events with filtering |
| `get_event_by_id(conn, id)` | Get single event |
| `insert_process_sample(conn, event_id, tier, samples)` | Insert ProcessSamples as JSON blob |
| `get_process_samples(conn, event_id)` | Get all ProcessSampleRecords for event |
| `update_event_status(conn, id, status, notes)` | Update event status/notes |
| `prune_old_data(conn, samples_days, events_days)` | Delete old reviewed/dismissed events |

---

## Module: daemon.py

**Purpose:** Main daemon orchestrating continuous monitoring, tier management, and pause detection.

### Classes

#### DaemonState
Runtime state dataclass tracking:
- `running`, `sample_count`, `event_count`
- `last_sample_time`, `current_stress` (now stores max_score)
- `elevated_since`, `critical_since` (for duration tracking)

Methods: `update_sample()`, `enter_elevated()`, `exit_elevated()`, `enter_critical()`, `exit_critical()`
Properties: `elevated_duration`, `critical_duration`

#### Daemon
Main orchestrator class. Key attributes:
- `config`, `state`, `notifier`
- `collector` - TopCollector for per-process sampling
- `ring_buffer` - RingBuffer for samples
- `tier_manager` - TierManager for state machine
- `_current_event_id` - Active escalation event (tier 2+)
- `_current_peak_tier`, `_current_peak_score` - Event tracking
- `_socket_server` - SocketServer for TUI streaming

Class constant: `SAMPLE_INTERVAL_MS = 1000` (1Hz sampling)

### Key Methods (Daemon)
| Method | Purpose |
|--------|---------|
| `__init__(config)` | Wire up TopCollector, TierManager, RingBuffer, Notifier |
| `_init_database()` | Initialize DB (extracted for testing) |
| `start()` | Init DB, write PID, start caffeinate, start socket server, run main loop |
| `stop()` | Stop socket server, cancel tasks, cleanup |
| `_handle_signal(sig)` | Handle SIGTERM/SIGINT |
| `_start_caffeinate()` | Prevent App Nap with `/usr/bin/caffeinate -i` |
| `_stop_caffeinate()` | Terminate caffeinate subprocess |
| `_write_pid_file()` | Write PID to `daemon.pid` |
| `_remove_pid_file()` | Remove PID file |
| `_check_already_running()` | Check if daemon already running via PID file |
| `_auto_prune()` | Daily auto-prune task |
| `_run_heavy_capture(capture)` | Run spindump/tailspin/logs in background |
| `_run_forensics(contents, duration)` | Full forensics on pause detection |
| `_handle_tier_change(action, tier)` | Update state and notify on tier transitions |
| `_save_event_sample(samples, tier)` | Save ProcessSamples to current event (tier 2/3) |
| `_handle_tier_action(action, samples)` | Handle tier transitions, create/finalize events |
| `_handle_pause(elapsed_ms, expected_ms)` | Handle detected pause - run forensics |
| `_main_loop()` | Main 1Hz loop: TopCollector -> buffer -> tiers -> broadcast |

### Main Loop Flow
```python
while not shutdown:
    # 1. Collect samples (takes ~1s due to top -l 2)
    samples = await collector.collect()
    tier = tier_manager.current_tier
    
    # 2. Push to ring buffer
    ring_buffer.push(samples, tier)
    
    # 3. Update tier manager with max score
    action = tier_manager.update(samples.max_score)
    if action:
        await _handle_tier_action(action, samples)
    
    # 4. Tier 3 continuous saving
    if tier == 3 and action != TIER3_ENTRY:
        _save_event_sample(samples, tier=3)
    
    # 5. Check for pause
    if samples.elapsed_ms > expected_ms * pause_threshold:
        await _handle_pause(samples.elapsed_ms, expected_ms)
    
    # 6. Broadcast to TUI
    if socket_server.has_clients:
        await socket_server.broadcast(samples, tier)
    
    # 7. Update state
    state.update_sample(samples.max_score)
```

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
SENTINEL (tier 1) --- score >= elevated (35) --> ELEVATED (tier 2)
    ^                                                  |
    |                                                  | score >= critical (65)
    |                                                  v
    <---- score < elevated (5s) ---- ELEVATED <---- CRITICAL (tier 3)
                                        ^               |
                                        +--- score < critical (5s)
```

Properties:
- `current_tier` - Returns 1, 2, or 3
- `peak_score` - Peak score since elevation (reset on tier1 return)
- `tier2_entry_time`, `tier3_entry_time` - Entry timestamps

Methods:
- `__init__(elevated_threshold, critical_threshold, deescalation_delay)`
- `update(score)` - Process score, return TierAction or None

---

## Module: ringbuffer.py

**Purpose:** Fixed-size ring buffer for process samples.

### Classes

#### RingSample
Dataclass: `samples` (ProcessSamples), `tier`
Timestamp accessed via `samples.timestamp`.

#### BufferContents
Dataclass (immutable): `samples` (tuple of RingSample)

#### RingBuffer
Main implementation using `collections.deque(maxlen=...)`.

Default: 30 samples (30 seconds at 1Hz).

Methods:
- `__init__(max_samples=30)`
- `__len__()` - Number of samples
- `is_empty` (property) - True if empty
- `samples` (property) - Copy of samples list
- `push(samples, tier)` - Add ProcessSamples to buffer
- `clear()` - Empty the buffer
- `freeze()` - Return immutable BufferContents copy

---

## Module: socket_server.py

**Purpose:** Unix domain socket server for push-based streaming to TUI.

### SocketServer
Push-based design: main loop calls `broadcast()` after each sample.

Protocol: Newline-delimited JSON over Unix socket at `daemon.sock`

Message types:
- `initial_state` - Sent on connect with recent buffer samples
- `sample` - Sent via broadcast with ProcessSamples data

Message fields (sample type):
- `type`, `timestamp`, `tier`
- `elapsed_ms`, `process_count`, `max_score`
- `rogues` - list of ProcessScore dicts
- `sample_count` - ring buffer size

Methods:
- `__init__(socket_path, ring_buffer)`
- `has_clients` (property) - For main loop optimization
- `start()` - Create socket, chmod for non-root TUI access
- `stop()` - Close connections, remove socket file
- `broadcast(samples, tier)` - Push ProcessSamples to all clients
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
- `write_ring_buffer(contents)` - Write ring_buffer.json with samples and rogues

### Functions
| Function | Purpose |
|----------|---------|
| `create_event_dir(events_dir, event_time)` | Create timestamped directory |
| `identify_culprits(contents)` | Identify top rogues from buffer samples |
| `capture_spindump(event_dir, timeout)` | Run `/usr/sbin/spindump` |
| `capture_tailspin(event_dir, timeout)` | Run `/usr/bin/tailspin save` |
| `capture_system_logs(event_dir, window, timeout)` | Run `/usr/bin/log show` |
| `run_full_capture(capture, window)` | Run all captures concurrently |

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
- `critical_stress(score, duration)` - Notify sustained critical (uses max_score)
- `elevated_entered(score)` - Notify entering elevated (if enabled)
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
Visual score meter with color coding (green->yellow->red at 30/60).
Shows `max_score` from ProcessSamples.

Methods: `update_score(score)`, `set_disconnected()`

#### SampleInfoPanel
Display tier, process count, sample count.

Methods: `update_info(tier, process_count, sample_count)`, `set_disconnected()`

#### ProcessesPanel
DataTable showing top rogue processes by score.

Columns: Command, Score, CPU%, Mem, Pageins, State

Methods: `update_rogues(rogues)`, `set_disconnected()`, `_format_bytes()`

#### EventsTable
DataTable showing recent events from database.

#### EventDetailScreen
Modal screen for viewing/editing single event.

#### EventsScreen
Full events list with filtering (unreviewed/all).

#### PauseMonitorApp
Main Textual App class.

Layout: 2x3 grid with stress gauge, sample info, events, processes

Methods:
- `__init__(config)`
- `on_mount()` - Connect DB, attempt socket connection, start refresh timer
- `on_unmount()` - Cancel tasks, disconnect socket, close DB
- `_try_socket_connect()` - Async socket connection
- `_read_socket_loop()` - Read messages and update UI
- `_set_disconnected(error)` - Update UI for disconnected state
- `_handle_socket_data(data)` - Process socket messages (extracts max_score, rogues)
- `compose()` - Build UI layout
- `_refresh_events()` - Refresh events table from DB
- `action_refresh()`, `action_show_events()`, `action_show_history()`

Bindings: q=quit, r=refresh, e=events, h=history

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-process scoring | Identifies specific culprit processes, not just "system is stressed" |
| TopCollector at 1Hz | `top -l 2` provides accurate CPU% deltas; powermetrics deprecated |
| 8-factor weighted scoring | Flexible, configurable, identifies different stress types |
| Rogue selection before scoring | Focus on suspicious processes, reduce noise |
| JSON blob storage (v7) | Flexible schema evolution, stores full ProcessSamples |
| TierManager uses max_score | Tier escalation based on worst process, not aggregate |
| Ring buffer for forensics | Capture pre-pause context without storage overhead |
| Tier hysteresis (5s delay) | Prevent oscillation between states |
| Unix socket for TUI | Real-time data without SQLite polling overhead |
| Push-based socket broadcast | Main loop calls broadcast(), no internal polling |
| Event status flags | Allow user triage; protect important events from pruning |
| WAL mode SQLite | Better concurrent read/write performance |
| TOML config format | Human-readable, standard Python tooling |
| XDG paths | Standard macOS/Linux config/data locations |

---

## Testing

### Test Files
| File | Coverage |
|------|----------|
| `tests/test_cli.py` | CLI commands |
| `tests/test_config.py` | Config loading/saving |
| `tests/test_daemon.py` | Daemon state, PID file, main loop |
| `tests/test_collector.py` | TopCollector parsing, scoring |
| `tests/test_storage.py` | Database operations, process samples |
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
- `sample_stress` - Sample data for testing
