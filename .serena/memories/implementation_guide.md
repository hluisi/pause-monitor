# Implementation Guide

> **Phase 5 COMPLETE (2026-01-22).** Redesign eliminated Sentinel; daemon now uses powermetrics directly.

**Last updated:** 2026-01-22 (Phase 5 complete - redesign done)

This document describes the actual implementation of pause-monitor, module by module. For the canonical design specification, see `design_spec`. For known gaps, see `unimplemented_features`.

## Architecture (Post-Redesign)

### Data Flow
- Single 100ms loop driven by powermetrics stream
- Ring buffer receives complete samples continuously
- TUI streams from Unix socket (not SQLite polling)
- SQLite stores only tier events (elevated bookmarks, pause forensics)

### Key Components
- `Daemon._main_loop()` - Main 10Hz processing loop
- `Daemon._calculate_stress()` - 8-factor stress from powermetrics
- `TierManager` - Tier state machine (extracted from Sentinel)
- `SocketServer` - Broadcasts ring buffer to TUI
- `SocketClient` - TUI receives real-time data

### Deleted (No Longer Exists)
- `Sentinel` class - Deleted entirely, use `TierManager` directly
- `calculate_stress()` function - Deleted, use `Daemon._calculate_stress()`
- `IOBaselineManager` class - Deleted
- `SamplePolicy` - Deleted
- `slow_interval_ms` config - Deleted

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
         |                           |                             |
         v                           v                             v
+------------------+        +------------------+          +------------------+
|   TierManager    |        |     Storage      |          |   SocketClient   |
|  (sentinel.py)   |        |   (storage.py)   |          | (socket_client)  |
+--------+---------+        +--------+---------+          +--------+---------+
         |                           ^                             ^
         v                           |                             |
+------------------+                 |                   +------------------+
|   RingBuffer     |                 |                   |   SocketServer   |
| (ringbuffer.py)  |-----------------+------------------>| (socket_server)  |
+------------------+                                     +------------------+
                                     |
+------------------+   +-------------+-------------+   +------------------+
|   Collector      |-->|                           |<--|   Forensics      |
| (collector.py)   |   |        Sample/Event       |   |  (forensics.py)  |
+------------------+   +---------------------------+   +------------------+
         ^                                                      ^
         |                                                      |
+------------------+                                   +------------------+
| PowermetricsStream                                  |  Capture tools   |
|  (streaming plist)                                  | (spindump, etc.) |
+------------------+                                   +------------------+

Supporting modules:
+------------------+  +------------------+  +------------------+
|     stress.py    |  |  notifications.py |  |   sleepwake.py  |
| (StressBreakdown)|  |  (macOS alerts)  |  | (pmset parsing)  |
+------------------+  +------------------+  +------------------+
```

**Data Flow:**
1. Daemon starts -> initializes TierManager + SocketServer + PowermetricsStream
2. Main loop (100ms) -> PowermetricsStream -> _calculate_stress() -> RingBuffer.push() -> TierManager.update()
3. SocketServer broadcasts ring buffer samples to TUI via Unix socket
4. Pause detection: latency_ratio > 2.0 -> freezes buffer -> Daemon runs forensics -> insert_event
5. TUI: Receives real-time data via SocketClient (not SQLite polling)

---

## Module: cli.py

**Purpose:** Click-based CLI entry point with commands for daemon, TUI, status, events, history, config, and service management.

### Functions
| Function | Purpose |
|----------|---------|
| `main()` | Click group entry point with version option |
| `daemon()` | Run background sampler via asyncio.run(run_daemon()) |
| `tui()` | Launch interactive Textual dashboard |
| `status()` | Quick health check - shows daemon status, last sample, recent events |
| `events()` | Group for event management; lists events with filtering |
| `events_show(event_id)` | Display full details of a specific event |
| `events_mark(event_id, ...)` | Change event status (reviewed/pinned/dismissed) or add notes |
| `history(hours, fmt)` | Query historical samples with table/json/csv output |
| `prune(...)` | Delete old data per retention policy |
| `config()` | Group for config subcommands |
| `config_show()` | Display current configuration |
| `config_edit()` | Open config.toml in editor |
| `config_reset()` | Reset to defaults |
| `install()` | Set up launchd service |
| `uninstall()` | Remove launchd service |

### Data Flow
- Commands load Config, open SQLite connection, call storage functions
- Daemon command calls `run_daemon()` which creates Daemon instance
- TUI command calls `run_tui(config)` which creates PauseMonitorApp

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
| `SentinelConfig` | Sentinel timing (fast_interval_ms=100, ring_buffer_seconds=30) |
| `TiersConfig` | Tier thresholds (elevated=15, critical=50) |
| `Config` | Main container with all sub-configs and path methods |

### Key Methods (Config)
| Method | Purpose |
|--------|---------|
| `config_dir` | `~/.config/pause-monitor/` |
| `config_path` | `~/.config/pause-monitor/config.toml` |
| `data_dir` | `~/.local/share/pause-monitor/` |
| `db_path` | `~/.local/share/pause-monitor/data.db` |
| `events_dir` | `~/.local/share/pause-monitor/events/` |
| `load()` | Load from TOML or return defaults |
| `save()` | Write to TOML |

### Data Flow
- All components receive Config at initialization
- Config paths determine database and event storage locations

---

## Module: daemon.py

**Purpose:** Main daemon orchestrating continuous monitoring, sampling, and pause detection.

### Classes
| Class | Purpose |
|-------|---------|
| `DaemonState` | Runtime state: sample_count, event_count, elevated/critical times, flags |
| `Daemon` | Main orchestrator - manages TierManager, RingBuffer, SocketServer, PowermetricsStream |

### Key Methods (Daemon)
| Method | Purpose |
|--------|---------|
| `__init__(config)` | Wire up all components: TierManager, RingBuffer, SocketServer, Notifier, etc. |
| `start()` | Initialize DB, write PID file, start caffeinate, start loops |
| `stop()` | Shutdown event, stop powermetrics, stop socket server, cleanup |
| `_main_loop()` | Main 10Hz async loop - stream powermetrics, calculate stress, push to buffer |
| `_calculate_stress(pm_result, latency_ratio)` | Calculate StressBreakdown with all 8 factors from powermetrics data |
| `_handle_tier_action(action, stress)` | Handle TierAction transitions, write bookmarks on tier2_exit, track peak_stress |
| `_handle_pause(actual, expected)` | Pause detection - freezes buffer, runs forensics, creates event |
| `_maybe_update_peak(stress)` | Track peak stress during elevated/critical tiers |
| `_run_forensics(capture)` | Background task for spindump/tailspin/logs |
| `_auto_prune()` | Periodic task to clean old data |

### Integration with TierManager and SocketServer
```python
# In __init__:
self.tier_manager = TierManager(config.tiers.elevated, config.tiers.critical)
self.ring_buffer = RingBuffer(max_samples=300)  # 30s at 100ms
self.socket_server = SocketServer(config.socket_path)

# In _main_loop:
stress = self._calculate_stress(pm_result, latency_ratio)
self.ring_buffer.push(stress, self.tier_manager.current_tier)
action = self.tier_manager.update(stress.total())
if action:
    self._handle_tier_action(action, stress)
self.socket_server.broadcast(sample_message)
```

### Data Flow
1. Daemon.start() -> init_database() + socket_server.start() + _main_loop()
2. _main_loop() iterates PowermetricsStream -> _calculate_stress() -> ring_buffer.push()
3. TierManager.update() called on each sample -> returns TierAction on transitions
4. SocketServer broadcasts samples to connected TUI clients
5. Pause detected -> _handle_pause() -> create Event + ForensicsCapture

---

## Module: collector.py

**Purpose:** Metrics collection via powermetrics subprocess and system calls.

### Classes
| Class | Purpose |
|-------|---------|
| `SystemMetrics` | Non-powermetrics data: load_avg, mem_available, swap, I/O counters, network |
| `StreamStatus` | PowermetricsStream state enum (STOPPED, STARTING, RUNNING, ERROR) |
| `PowermetricsResult` | Parsed powermetrics data: cpu_pct, cpu_temp, cpu_freq, throttled, gpu_pct, wakeups_per_sec, pageins_per_sec, top_cpu_processes, top_pagein_processes |
| `PowermetricsStream` | Async streaming plist reader from powermetrics subprocess |

### Key Functions
| Function | Purpose |
|----------|---------|
| `get_core_count()` | Return CPU core count via os.cpu_count() |
| `get_system_metrics()` | Collect load_avg, memory, I/O, network via os/psutil |
| `_get_memory_available()` | Memory available in bytes |
| `_get_swap_used()` | Swap bytes used |
| `_get_io_counters()` | Disk I/O bytes (read, write) |
| `_get_network_counters()` | Network bytes (sent, recv) |
| `parse_powermetrics_sample(data)` | Parse plist bytes -> PowermetricsResult |
| `_extract_cpu_usage(plist)` | Extract CPU % from processor dict |
| `_extract_cpu_freq(plist)` | Extract CPU frequency from processor dict |

### PowermetricsStream Details
- Uses `-f plist` streaming output for lower latency
- Reads plist documents separated by `</plist>` markers
- Handles subprocess lifecycle (start/stop/restart on error)

### Data Flow
1. Daemon calls PowermetricsStream.start()
2. `async for pm_result in stream.read_samples()` yields parsed samples
3. get_system_metrics() called separately for non-powermetrics data
4. Both combined in Daemon._collect_sample()

---

## Module: stress.py

**Purpose:** Stress breakdown dataclass and memory pressure utilities. (Note: `calculate_stress()` and `IOBaselineManager` were deleted in Phase 5 redesign.)

### Classes
| Class | Purpose |
|-------|---------|
| `MemoryPressureLevel` | Enum: NORMAL, WARN, CRITICAL with from_percent() class method |
| `StressBreakdown` | Dataclass with per-factor scores + total() method |

### Key Functions
| Function | Purpose |
|----------|---------|
| `get_memory_pressure_fast()` | Get memory available % via sysctl (fast path) |

### Stress Factors (in StressBreakdown)
| Factor | Max Points | Calculation |
|--------|------------|-------------|
| `load` | 30 | Based on load_avg / core_count ratio |
| `memory` | 30 | Based on memory available % |
| `thermal` | 10 | 10 if throttled, 0 otherwise |
| `latency` | 20 | Based on actual/expected interval ratio |
| `io` | 10 | Based on I/O rate from powermetrics |
| `gpu` | 20 | Based on GPU utilization % |
| `wakeups` | 10 | Based on idle wakeups per second |
| `pageins` | 30 | Based on swap pageins/sec (CRITICAL for pause detection) |

### Deleted in Phase 5 Redesign
- `calculate_stress()` function - Deleted, use `Daemon._calculate_stress()` instead
- `IOBaselineManager` class - Deleted, I/O stress now calculated directly from powermetrics

### Data Flow
- `Daemon._calculate_stress()` creates StressBreakdown from PowermetricsResult
- StressBreakdown.total() returns 0-100 score for tier decisions

---

## Module: storage.py

**Purpose:** SQLite database operations with auto-migration and pruning.

### Constants
| Constant | Value |
|----------|-------|
| `SCHEMA_VERSION` | 5 (added stress_pageins column) |
| `VALID_EVENT_STATUSES` | "unreviewed", "reviewed", "pinned", "dismissed" |
| `SCHEMA` | SQL for tables: samples, events, process_samples |

### Classes
| Class | Purpose |
|-------|---------|
| `Sample` | Dataclass for metrics sample (matches design doc field names) |
| `Event` | Dataclass for pause event with status, culprits, notes, peak_stress |

### Key Functions
| Function | Purpose |
|----------|---------|
| `init_database(path)` | Create tables, enable WAL, run migrations |
| `get_connection(path)` | Return sqlite3.Connection with row factory |
| `get_schema_version(conn)` | Read current schema version |
| `migrate_add_event_status(conn)` | Add status column to events |
| `migrate_add_stress_columns(conn)` | Add GPU/wakeups stress columns |
| `insert_sample(conn, sample)` | Insert sample, return ID |
| `get_recent_samples(conn, limit)` | Get newest samples |
| `insert_event(conn, event)` | Insert event, return ID |
| `get_events(conn, ...)` | Get events with optional filtering |
| `get_event_by_id(conn, id)` | Get single event by ID |
| `update_event_status(conn, id, status, notes)` | Update event status/notes |
| `prune_old_data(conn, samples_days, events_days)` | Delete old data, respecting pinned/unreviewed |

### Data Flow
- Daemon calls insert_sample() in _collect_sample()
- Daemon calls insert_event() in _handle_pause_from_sentinel()
- CLI/TUI call get_recent_samples(), get_events() for display
- Auto-prune runs periodically, respects status flags

---

## Module: forensics.py

**Purpose:** Capture diagnostic data when pause detected.

### Classes
| Class | Purpose |
|-------|---------|
| `ForensicsCapture` | Context manager for capturing artifacts to event directory |

### Key Functions
| Function | Purpose |
|----------|---------|
| `create_event_dir(base, timestamp)` | Create timestamped event directory |
| `identify_culprits(contents)` | Analyze ring buffer for likely culprits by factor |
| `capture_spindump(dir, window)` | Run spindump command |
| `capture_tailspin(dir)` | Run tailspin save command |
| `capture_system_logs(dir, window)` | Export log show output |
| `run_full_capture(capture, window)` | Run all capture steps |

### ForensicsCapture Methods
| Method | Purpose |
|--------|---------|
| `write_metadata(data)` | Write metadata.json with event details |
| `write_process_snapshot(snapshot)` | Write process list from ring buffer |
| `write_text_artifact(name, text)` | Write text file to event dir |
| `write_binary_artifact(name, data)` | Write binary file to event dir |
| `write_ring_buffer(contents)` | Write ring buffer samples + snapshots as JSON |

### Culprit Identification Logic
```python
# From identify_culprits():
# 1. Average stress factors over all samples in buffer
# 2. For factors >= threshold (10):
#    - memory stress -> top memory consumers from snapshots
#    - load stress -> top CPU consumers from snapshots
#    - gpu stress -> top CPU consumers (proxy)
#    - io/wakeups -> empty list (per-process not tracked)
# 3. Return sorted by score descending
```

### Data Flow
1. Pause detected -> Daemon._handle_pause_from_sentinel()
2. buffer.freeze() -> immutable copy of samples + snapshots
3. ForensicsCapture created with event_dir
4. write_ring_buffer(contents) saves buffer data
5. identify_culprits(contents) extracts likely causes
6. run_full_capture() runs spindump/tailspin/logs in background

---

## Module: notifications.py

**Purpose:** macOS notification center integration.

### Classes
| Class | Purpose |
|-------|---------|
| `NotificationType` | Enum: PAUSE_DETECTED, CRITICAL_STRESS, ELEVATED, FORENSICS_COMPLETED |
| `Notifier` | Manages notifications based on AlertsConfig settings |

### Key Functions
| Function | Purpose |
|----------|---------|
| `send_notification(title, message, sound)` | Send via osascript display notification |

### Notifier Methods
| Method | Purpose |
|--------|---------|
| `pause_detected(duration, event_dir)` | Notify of pause with duration |
| `critical_stress(stress, duration)` | Notify of sustained critical stress |
| `elevated_entered(stress)` | Notify entering elevated state (if enabled) |
| `forensics_completed(event_dir)` | Notify forensics capture complete |

### Data Flow
- Daemon creates Notifier(config.alerts)
- Called from _handle_tier_change() and _handle_pause_from_sentinel()
- Respects AlertsConfig flags (enabled, sound, per-notification type)

---

## Module: sleepwake.py

**Purpose:** Detect system sleep/wake to exclude false pause detections.

### Classes
| Class | Purpose |
|-------|---------|
| `SleepWakeType` | Enum: SLEEP, WAKE |
| `SleepWakeEvent` | Dataclass: timestamp, type, reason |
| `PauseEvent` | Dataclass for detected pause: expected, actual intervals |
| `PauseDetector` | Detect pauses via timing anomalies with sleep exclusion |

### Key Functions
| Function | Purpose |
|----------|---------|
| `parse_pmset_log(log_text)` | Parse pmset -g log output for sleep/wake events |
| `get_recent_sleep_events(seconds)` | Get recent events from pmset |
| `was_recently_asleep(within_seconds)` | Check if system woke recently |

### PauseDetector
```python
# check(actual_interval) returns PauseEvent if:
# 1. actual_interval / expected_interval > threshold (default 2.0)
# 2. NOT was_recently_asleep(within=actual_interval)
```

### Data Flow
- Daemon uses PauseDetector for legacy pause detection
- Sentinel also calls was_recently_asleep() before reporting pauses
- Filters out false positives from sleep/wake cycles

---

## Module: ringbuffer.py

**Purpose:** Fixed-size ring buffer for stress samples with process snapshot support.

### Classes
| Class | Purpose |
|-------|---------|
| `ProcessInfo` | Single process: pid, name, cpu_pct, memory_mb |
| `ProcessSnapshot` | Snapshot at time T: timestamp, by_cpu list, by_memory list |
| `RingSample` | Single stress sample: timestamp, stress (StressBreakdown), tier |
| `BufferContents` | Immutable copy: samples list, snapshots list |
| `RingBuffer` | Main ring buffer implementation |

### RingBuffer Methods
| Method | Purpose |
|--------|---------|
| `__init__(max_samples)` | Create buffer (default 300 = 30s at 100ms) |
| `samples` | Property returning copy of samples list |
| `snapshots` | Property returning copy of snapshots list |
| `push(stress, tier)` | Add sample, evict oldest if full |
| `snapshot_processes()` | Capture current process state (called on tier2_entry, tier2_peak, tier3_entry) |
| `freeze()` | Return immutable BufferContents copy |
| `clear_snapshots()` | Clear snapshots on de-escalation |

### Data Flow
1. Daemon._main_loop() calls buffer.push() every 100ms
2. On tier escalation, _handle_tier_action() calls buffer.snapshot_processes()
3. On pause detection, buffer.freeze() captures state
4. On tier2_exit, buffer.clear_snapshots() resets

---

## Module: socket_server.py

**Purpose:** Unix socket server for broadcasting ring buffer samples to TUI clients.

### Classes
| Class | Purpose |
|-------|---------|
| `SocketServer` | Manages Unix socket, accepts connections, broadcasts messages |

### Key Methods
| Method | Purpose |
|--------|---------|
| `__init__(socket_path)` | Initialize with path to Unix socket |
| `start()` | Create socket, start accepting connections |
| `stop()` | Close all connections, remove socket file |
| `broadcast(message)` | Send JSON message to all connected clients |

### Message Format
```python
{
    "type": "sample",
    "timestamp": "2026-01-22T12:34:56.789",
    "stress": {"load": 5, "memory": 10, ...},  # StressBreakdown as dict
    "tier": 1  # Current tier level
}
```

### Data Flow
1. Daemon calls socket_server.start() during startup
2. TUI clients connect via SocketClient
3. Each sample, Daemon calls socket_server.broadcast()
4. On shutdown, socket_server.stop() cleans up

---

## Module: socket_client.py

**Purpose:** Unix socket client for TUI to receive real-time samples from daemon.

### Classes
| Class | Purpose |
|-------|---------|
| `SocketClient` | Connects to daemon socket, receives sample stream |

### Key Methods
| Method | Purpose |
|--------|---------|
| `__init__(socket_path)` | Initialize with path to Unix socket |
| `connect()` | Establish connection to daemon |
| `disconnect()` | Close connection |
| `read_message()` | Read next JSON message from socket |
| `async for message in client` | Async iteration over messages |

### Data Flow
1. TUI creates SocketClient with daemon's socket path
2. On mount, TUI calls client.connect()
3. TUI receives messages via async iteration
4. Messages parsed and used to update widgets
5. On unmount, client.disconnect()

---

## Module: sentinel.py

**Purpose:** Tier state machine for stress level transitions. (Note: Sentinel class was deleted in Phase 5 redesign; only TierManager remains.)

### Classes
| Class | Purpose |
|-------|---------|
| `Tier` | Enum: SENTINEL (1), ELEVATED (2), CRITICAL (3) |
| `TierAction` | Enum: TIER2_ENTRY, TIER2_EXIT, TIER2_PEAK, TIER3_ENTRY, TIER3_EXIT |
| `TierManager` | Manages tier transitions with hysteresis |

### TierManager State Machine
```
                     stress >= critical_threshold
    +-------- SENTINEL ---------> CRITICAL
    |   ^                            |
    |   | stress < elevated_threshold|
    |   | (for 5 seconds)            |
    |   |                            |
    |   +-------- ELEVATED <---------+
    |                  ^      stress < critical_threshold
    |                  |      (for 5 seconds)
    +------------------+
    stress >= elevated_threshold
```

### TierManager Methods
| Method | Purpose |
|--------|---------|
| `__init__(elevated_threshold, critical_threshold)` | Initialize thresholds and timers |
| `current_tier` | Property returning current Tier |
| `peak_stress` | Property returning peak stress since elevation |
| `update(stress_total)` | Process stress value, return TierAction if state change |

### TierAction Returns from update():
- `TierAction.TIER2_ENTRY` - entered elevated tier
- `TierAction.TIER2_EXIT` - left elevated tier (after 5s hysteresis)
- `TierAction.TIER3_ENTRY` - entered critical tier
- `TierAction.TIER3_EXIT` - left critical tier (after 5s hysteresis)
- `TierAction.TIER2_PEAK` - new peak stress in elevated tier
- `None` - no state change

### Usage (from Daemon._main_loop)
```python
# TierManager is used directly by Daemon, no Sentinel wrapper:
action = self.tier_manager.update(stress.total())
if action:
    self._handle_tier_action(action, stress)
```

### Deleted in Phase 5 Redesign
The `Sentinel` class was deleted entirely. Its responsibilities were absorbed by `Daemon._main_loop()`:
- Fast loop timing -> now driven by powermetrics 100ms stream
- Stress calculation -> `Daemon._calculate_stress()`
- Buffer management -> direct `RingBuffer.push()` calls
- Pause detection -> `Daemon._handle_pause()`

---

## Module: sysctl.py

**Purpose:** Direct sysctl access via ctypes for fast metrics collection.

### Functions
| Function | Purpose |
|----------|---------|
| `sysctl_int(name)` | Read integer sysctl value by MIB name |

### Implementation Details
```python
# Uses libc.sysctlbyname() directly via ctypes
# Returns int for valid sysctls, None for missing/failed
# Used by collect_fast_metrics() for:
#   - kern.memorystatus_level (memory pressure)
#   - vm.page_free_count (alternative memory metric)
```

### Data Flow
- Called by sentinel.collect_fast_metrics() in fast loop
- Much faster than subprocess calls (~20us vs ~10ms)

---

## Module: tui/app.py

**Purpose:** Textual-based interactive dashboard with real-time socket streaming.

### Classes
| Class | Purpose |
|-------|---------|
| `StressGauge` | Visual stress meter widget with color coding |
| `MetricsPanel` | Display current CPU/memory/load metrics |
| `EventsTable` | DataTable showing recent events |
| `EventDetailScreen` | Modal screen for viewing/editing single event |
| `EventsScreen` | Full events list with filtering |
| `PauseMonitorApp` | Main Textual App class |

### PauseMonitorApp Methods
| Method | Purpose |
|--------|---------|
| `__init__(config)` | Store config, create SocketClient |
| `on_mount()` | Connect to socket, start message receiver |
| `on_unmount()` | Disconnect socket, close DB |
| `compose()` | Build UI layout |
| `_handle_message(msg)` | Process incoming socket message, update widgets |
| `action_refresh()` | Manual refresh keybinding |
| `action_show_events()` | Show events screen |
| `action_show_history()` | (Future) Show history screen |

### Data Flow (Post-Redesign)
1. App creates SocketClient, connects to daemon's Unix socket
2. Async task receives messages from socket stream
3. Each message triggers _handle_message() to update widgets
4. Real-time updates without SQLite polling
5. Events screen still queries SQLite for historical events

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single 10Hz loop (Post-Redesign) | Powermetrics stream drives timing; eliminates separate fast/slow loops |
| Unix socket for TUI streaming | Real-time data without SQLite polling overhead |
| TierManager extracted from Sentinel | Reusable state machine; Sentinel class deleted |
| Ring buffer for forensics | Capture pre-pause context without storage overhead |
| Tier hysteresis (5s delay) | Prevent oscillation between states |
| Streaming powermetrics | Lower latency than exec-per-sample approach |
| Event status flags | Allow user triage; protect important events from pruning |
| WAL mode SQLite | Better concurrent read/write performance |
| TOML config format | Human-readable, standard Python tooling |
| XDG paths | Standard macOS/Linux config/data locations |
| StressBreakdown as canonical type | Single source of truth, imported by storage.py |
| SQLite for tier events only | Samples flow through socket; only bookmarks/forensics persisted |

---

## Testing

### Test Files
| File | Coverage |
|------|----------|
| `tests/test_cli.py` | CLI commands |
| `tests/test_config.py` | Config loading/saving |
| `tests/test_daemon.py` | Daemon state, PID file, Sentinel integration |
| `tests/test_collector.py` | Metrics collection, parsing |
| `tests/test_stress.py` | Stress calculation |
| `tests/test_storage.py` | Database operations, migrations |
| `tests/test_forensics.py` | Forensics capture |
| `tests/test_notifications.py` | Notification sending |
| `tests/test_sleepwake.py` | Sleep/wake detection |
| `tests/test_sentinel.py` | Sentinel loops, tier transitions |
| `tests/test_ringbuffer.py` | Ring buffer operations |
| `tests/test_integration.py` | End-to-end tests |
| `tests/conftest.py` | Shared fixtures |

### Running Tests
```bash
uv run pytest                    # All tests
uv run pytest -v                 # Verbose
uv run pytest tests/test_sentinel.py  # Specific module
uv run pytest -k "tier"          # Pattern matching
```

### What's Covered
- Unit tests for all core classes
- Tier state machine transitions with hysteresis
- Ring buffer push/freeze/clear operations
- Database schema migrations
- Event status workflows
- Stress calculation edge cases
- Config serialization round-trip
