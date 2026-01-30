# Implementation Guide

> **Phase 8 COMPLETE (2026-01-29).** Per-process band tracking with ProcessTracker; SCHEMA_VERSION=9.

**Last updated:** 2026-01-29

This document describes the actual implementation. For design spec, see `design_spec`. For gaps, see `unimplemented_features`.

## Architecture

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
+------------------+        +------------------+          +------------------+
         |                           ^                             ^
         | ProcessSamples            |                             |
         v                           |                             |
+------------------+                 |                   +------------------+
|  ProcessTracker  |-----------------+------------------>|   SocketServer   |
|   (tracker.py)   |                                     | (socket_server)  |
+------------------+                                     +------------------+
         |
         v
+------------------+        +------------------+
|   RingBuffer     |        |   Forensics      |
| (ringbuffer.py)  |------->|  (forensics.py)  |
+------------------+        +------------------+
```

---

## Module: collector.py

**Purpose:** Per-process data collection via `top` at 1Hz.

### Dataclasses

**ProcessScore** — Single scored process:
- `pid`, `command`, `cpu`, `state`
- `mem`, `cmprs`, `pageins`, `csw`, `sysbsd`, `threads`
- `score` (0-100 weighted)
- `categories` (frozenset of selection reasons)
- `captured_at` (float timestamp)
- Methods: `to_dict()`, `from_dict()`

**ProcessSamples** — Collection from one sample:
- `timestamp` (datetime), `elapsed_ms`, `process_count`
- `max_score` (highest rogue score)
- `rogues` (list of ProcessScore)
- Methods: `to_json()`, `from_json()`

### TopCollector

Command: `top -l 2 -s 1 -stats pid,command,cpu,state,mem,cmprs,threads,csw,sysbsd,pageins`

Methods:
- `collect()` → ProcessSamples
- `_parse_top_output(raw)` → list of process dicts (uses **second** sample for accurate deltas)
- `_select_rogues(processes)` → filtered list based on config
- `_score_process(proc)` → weighted score 0-100
- `_normalize_state(state)` → 0-1 multiplier

### Scoring Formula

8 factors with configurable weights (sum = 100):
- `cpu` (25): CPU% / 100
- `state` (20): stuck=1.0, zombie=0.8, halted=0.6, stopped=0.4, else=0.0
- `pageins` (15): pageins / 1000
- `mem` (15): mem / 8GB
- `cmprs` (10): compressed / 1GB
- `csw` (10): context switches / 100k
- `sysbsd` (5): syscalls / 100k
- `threads` (0): disabled by default

Score = sum(normalized × weight), capped at 100, then multiplied by state multiplier, then multiplied by multi-category bonus (1.0 + 0.1 × max(0, category_count - 2)).

---

## Module: daemon.py

**Purpose:** Main daemon orchestrating monitoring loop.

### DaemonState

Tracks runtime state:
- `running`, `sample_count`, `event_count`
- `last_sample_time`, `current_score` (max_score from ProcessSamples)
- `elevated_since`, `critical_since`
- Method: `update_sample(score)`

### Daemon

**Key attributes:**
- `collector` — TopCollector
- `ring_buffer` — RingBuffer
- `tracker` — ProcessTracker (manages per-process event lifecycle)
- `boot_time` — System boot time (via `get_boot_time()`)
- `_socket_server` — SocketServer
- `_conn` — SQLite connection
- `_last_forensics_time` — For score-based forensics debouncing

**Main loop flow:**
```python
while not shutdown:
    samples = await collector.collect()
    tracker.update(samples.rogues)  # Per-process band tracking
    ring_buffer.push(samples)
    if timing_ratio > pause_threshold:
        await _handle_pause(...)
    if max_score >= bands.high:  # Score-based forensics
        await _run_forensics(..., trigger="score")
    await socket_server.broadcast(samples)
```

**Background tasks:**
- `_auto_prune()` — Daily pruning of old events
- `_hourly_system_sample()` — Saves full ProcessSamples hourly for trend analysis

---

## Module: tracker.py

**Purpose:** Per-process band tracking with event lifecycle management.

### Constants
- `SNAPSHOT_ENTRY`, `SNAPSHOT_EXIT`, `SNAPSHOT_CHECKPOINT`

### TrackedProcess (dataclass)

In-memory state for a tracked process:
- `event_id` — Database row ID
- `pid`, `command`
- `peak_score` — Highest score seen
- `last_checkpoint` — Timestamp of last checkpoint snapshot

### ProcessTracker

Manages per-process band state and database persistence.

**Constructor args:**
- `conn` — SQLite connection
- `bands` — BandsConfig
- `boot_time` — System boot timestamp

**Key methods:**
- `_restore_open_events()` — Restore state from DB on daemon restart
- `update(scores: list[ProcessScore])` — Main update method called each sample
- `_open_event(score)` — Create new event when process enters bad state
- `_close_event(pid, exit_time, exit_score)` — Close event when process exits bad state
- `_update_peak(score)` — Update peak when score increases
- `_insert_checkpoint(score, tracked)` — Periodic checkpoint snapshots

**Tracking rules:**
- Processes enter tracking when `score >= tracking_threshold` (default: elevated band = 40)
- Processes exit tracking when score drops below threshold OR process disappears
- Checkpoints saved every `checkpoint_interval` seconds (default: 30) while in bad state
- Band transitions logged with colored output

---

## Module: boottime.py

**Purpose:** Detect system boot time via sysctl.

### Function

`get_boot_time() -> int` — Returns Unix timestamp of system boot.

Uses `sysctl -n kern.boottime` and parses `sec = NNNN` from output.

---

## Module: storage.py

**Purpose:** SQLite with WAL mode.

### Constants
- `SCHEMA_VERSION = 9`

### Tables (v9)

**daemon_state** — Key-value store for daemon state:
- `key` (TEXT PRIMARY KEY), `value` (TEXT), `updated_at` (REAL)

**process_events** — One row per process tracking event:
- `id` (INTEGER PRIMARY KEY), `pid`, `command`, `boot_time`
- `entry_time`, `exit_time` (NULL if still tracking)
- `entry_band`, `peak_band`, `peak_score`, `peak_snapshot` (JSON)

**process_snapshots** — Snapshots during tracking:
- `id`, `event_id` (FK), `snapshot_type` (entry/exit/checkpoint), `snapshot` (JSON)

**system_samples** — Hourly full system samples for trend analysis:
- `id`, `captured_at`, `data` (JSON ProcessSamples)
- Pruned after 7 days

### Key Functions

**Process event CRUD:**
- `create_process_event(...)` → event_id
- `get_open_events(conn, boot_time)` → list of dicts
- `get_process_events(conn, boot_time, time_cutoff, limit)` → list of dicts
- `get_process_event_detail(conn, event_id)` → dict or None
- `close_process_event(conn, event_id, exit_time)`
- `update_process_event_peak(conn, event_id, peak_score, peak_band, peak_snapshot)`
- `insert_process_snapshot(conn, event_id, snapshot_type, snapshot)`

**System samples:**
- `insert_system_sample(conn, data)`
- `get_last_system_sample_time(conn)` → float or None
- `prune_system_samples(conn, days)` → deleted count

**Pruning:**
- `prune_old_data(conn, events_days, system_samples_days)` → (events_deleted, samples_deleted)

---

## Module: config.py

**Purpose:** TOML configuration.

### Key Classes

**ScoringWeights** — 8 factor weights (sum 100)

**StateMultipliers** — Post-score multipliers by state:
- idle=0.5, sleeping=0.7, running=0.9, uninterruptible=0.95, stuck=1.0

**RogueSelectionConfig** — Per-category selection:
- Each category: enabled, count, threshold
- State selection: enabled, count, states list

**BandsConfig** — Band thresholds and behavior triggers:
- `low=20`, `medium=40`, `elevated=60`, `high=80`, `critical=100`
- `tracking_band` = "elevated" (threshold for per-process tracking)
- `forensics_band` = "high" (threshold for forensics capture)
- `checkpoint_interval` = 30 (seconds between checkpoint snapshots)
- `forensics_cooldown` = 60 (seconds between score-based forensics)
- Methods: `get_band(score)`, `get_threshold(band)`, `tracking_threshold`, `forensics_threshold`

### Config Paths
- config: `~/.config/pause-monitor/config.toml`
- data: `~/.local/share/pause-monitor/`
- db: `~/.local/share/pause-monitor/data.db`
- socket: `~/.local/share/pause-monitor/daemon.sock`

---

## Module: ringbuffer.py

**Purpose:** Fixed-size circular buffer for recent samples.

### Classes
- `RingSample` — ProcessSamples wrapper (no tier field anymore)
- `BufferContents` — Immutable snapshot with samples list
- `RingBuffer` — deque(maxlen=30)

Methods: `push(samples)`, `freeze()`, `clear()`, `capacity`, `is_empty`

---

## Module: socket_server.py / socket_client.py

**Purpose:** Real-time TUI streaming.

### Protocol
Newline-delimited JSON over Unix socket.

**Message types:**
- `initial_state` — Ring buffer state on connect (samples list, max_score, sample_count)
- `sample` — ProcessSamples data per sample

**SocketServer methods:**
- `start()`, `stop()`
- `broadcast(samples)` — Push to all clients
- `has_clients` property — For main loop optimization

---

## Module: forensics.py

**Purpose:** Diagnostic capture on pause or high score.

### ForensicsCapture
- `write_metadata()`, `write_process_snapshot()`
- `write_ring_buffer()` — Serializes ProcessSamples
- `write_text_artifact()`, `write_binary_artifact()`

### Functions
- `create_event_dir()` — Create timestamped event directory
- `capture_spindump()`, `capture_tailspin()`, `capture_system_logs()`
- `identify_culprits(contents)` — Top rogues from buffer
- `run_full_capture(capture, ...)` — Run all heavy captures

### Triggers
- **Pause detection:** When timing ratio exceeds threshold
- **Score-based:** When max_score >= high band (80), with 60s cooldown

---

## Module: tui/app.py

**Purpose:** Real-time monitoring dashboard (single screen, socket-only).

### Philosophy
- TUI = Real-time window into daemon state, nothing more
- Display what daemon sends via socket — no database queries
- CLI is for investigation; TUI is for "what's happening now"
- Single-screen dashboard — no page switching

### Widgets

**HeaderBar** — Score gauge + 30-second sparkline + stats:
- Reactive properties: `score`, `connected`
- Shows: STRESS bar, score/100, tier name, timestamp, process count, sample count

**ProcessTable** — Rogue processes with full metrics:
- CSS Grid layout with 9 columns (trend, process, score, cpu, mem, pgin, csw, state, why)
- 5 score bands with colors: critical (red), high (orange), elevated (yellow), medium (default), low (green)
- Decay persistence: processes stay visible 10s after dropping (dimmed)
- Trend symbols: ▲ escalating, ● steady, ▽ declining, ○ decayed

**TrackedEventsPanel** — Tracked processes (active and historical):
- Tracks by command name (not PID) to deduplicate
- Shows: Time, Process, Peak score, Duration, Why (categories), Status
- Active entries shown first, then history (max 15)

**ActivityLog** — System tier transitions:
- Logs: CRITICAL/ELEVATED/NORMAL transitions with timestamps
- Max 15 entries

**PauseMonitorApp** — Main app:
- Connects to daemon via SocketClient
- Handles initial_state and sample messages
- Updates all widgets from socket data

### Data Flow
```
Daemon (1Hz) → Socket → TUI
                 │
                 ├─→ HeaderBar.update_from_sample(...)
                 ├─→ ProcessTable.update_rogues(rogues, timestamp)
                 ├─→ TrackedEventsPanel.update_tracking(rogues, timestamp)
                 └─→ ActivityLog.check_transitions(score)
```

**No database queries in TUI.** Everything comes from socket.

---

## Deleted Components

These no longer exist:
- `sentinel.py` — TierManager replaced by ProcessTracker (band-based, not tier-based)
- `stress.py` — Replaced by per-process scoring in collector.py
- `StressBreakdown` — Use ProcessScore.score
- `PowermetricsStream` — Use TopCollector
- `TierManager`, `Tier`, `TierAction` enums — Deleted
- `calculate_stress()` — Deleted
- `IOBaselineManager` — Deleted
- `EventsScreen`, `EventDetailScreen`, `HistoryScreen` — TUI simplified to single screen
- `StressGauge`, `SampleInfoPanel`, `ProcessesPanel`, `EventsTable` — Replaced by new widgets
- `Event` dataclass in storage — Replaced by dict-based functions

---

## Testing

| File | Coverage |
|------|----------|
| test_collector.py | TopCollector parsing, scoring |
| test_daemon.py | Daemon state, main loop |
| test_tracker.py | ProcessTracker band tracking |
| test_storage.py | Database operations |
| test_ringbuffer.py | Ring buffer operations |
| test_socket_*.py | Socket server/client |
| test_forensics.py | Forensics capture |
| test_boottime.py | Boot time detection |
| test_tui.py | TUI widgets |
| test_tui_connection.py | TUI socket connection |

Run: `uv run pytest`
