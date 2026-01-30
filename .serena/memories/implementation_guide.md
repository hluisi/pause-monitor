# Implementation Guide

### LibprocCollector (Default)

**Data source:** Direct syscalls to `/usr/lib/libproc.dylib` via ctypes

**Module:** `libproc.py` — ctypes bindings for libproc.dylib

**APIs used:**
- `proc_pid_rusage(RUSAGE_INFO_V4)` — CPU time, memory, disk I/O, energy, wakeups
- `proc_pidinfo(PROC_PIDTASKINFO)` — Context switches, syscalls, threads
- `proc_pidinfo(PROC_PIDTBSDINFO)` — Process state, command name
- `proc_listallpids()` — List all PIDs
- `mach_timebase_info()` — Apple Silicon time unit conversion

**Benefits over TopCollector:**
- ~10-50ms per collection vs ~2s for top
- No subprocess spawn overhead
- No 50% data waste (maintains state for CPU% deltas)
- Access to more metrics (disk I/O, energy, wakeups, instructions, cycles)

**CPU% calculation:**
- Stores previous CPU time per PID in `_prev_samples` dict
- First sample: all processes show 0% CPU (no baseline)
- Subsequent samples: CPU% = (delta_cpu_time / delta_wall_time) × 100

**Apple Silicon note:**
- CPU times from rusage are in mach_absolute_time units
- Use `mach_timebase_info()` to convert: `(abstime × numer) // denom`
- Intel: (1, 1) — already nanoseconds
- Apple Silicon: (125, 3) — ~41.67ns per tick

### TopCollector (Legacy)

Kept for backwards compatibility. Use `collector = "top"` in config to enable.

**Problems:**
1. Subprocess spawn overhead every 2 seconds
2. First sample always invalid (CPU% needs delta) — wastes 50% of work
3. Text parsing is fragile
4. Missing: disk I/O, energy, instructions, cycles, wakeups, GPU

### Configuration

```toml
[sentinel]
collector = "libproc"  # or "top" for legacy
```

See `libproc_and_iokit_research` memory for complete API documentation

**Data source:** Direct syscalls to `/usr/lib/libproc.dylib` via ctypes

**APIs used:**
- `proc_pid_rusage(RUSAGE_INFO_V4)` — CPU time, memory, disk I/O, energy, wakeups
- `proc_pidinfo(PROC_PIDTASKINFO)` — Context switches, syscalls, threads
- `sysctl(KERN_PROC)` — Process state, listing
- IOKit (optional) — Per-process GPU time

**See `libproc_and_iokit_research` memory for complete API documentation.**

### Dataclasses

**ProcessScore** — Single scored process:
- `pid`, `command`, `cpu`, `state`
- `mem`, `cmprs`, `pageins`, `csw`, `sysbsd`, `threads`
- `score` (0-100 weighted)
- `categories` (frozenset of selection reasons)
- `captured_at` (float timestamp)
- Future: `disk_read`, `disk_write`, `energy`, `instructions`, `cycles`, `gpu_time`
- Methods: `to_dict()`, `from_dict()`

**ProcessSamples** — Collection from one sample:
- `timestamp` (datetime), `elapsed_ms`, `process_count`
- `max_score` (highest rogue score)
- `rogues` (list of ProcessScore)
- Methods: `to_json()`, `from_json()`

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
- `collector` — LibprocCollector (default) or TopCollector (legacy)
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

### Config Paths
- config: `~/.config/pause-monitor/config.toml`
- data: `~/.local/share/pause-monitor/`
- db: `~/.local/share/pause-monitor/data.db`
- socket: `~/.local/share/pause-monitor/daemon.sock`

---

## Module: ringbuffer.py

**Purpose:** Fixed-size circular buffer for recent samples.

### Classes
- `RingSample` — ProcessSamples wrapper
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

---

## Module: forensics.py

**Purpose:** Diagnostic capture on pause or high score.

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

**No database queries in TUI.** Everything comes from socket.

---

## Deleted Components

These no longer exist:
- `sentinel.py` — TierManager replaced by ProcessTracker
- `stress.py` — Replaced by per-process scoring in collector.py
- `PowermetricsStream` — Was replaced by TopCollector, now being replaced by LibprocCollector
- `TierManager`, `Tier`, `TierAction` enums — Deleted
- `calculate_stress()` — Deleted
- `IOBaselineManager` — Deleted

---

## Testing

| File | Coverage |
|------|----------|
| test_libproc.py | libproc.dylib bindings |
| test_collector.py | Collector parsing, scoring, LibprocCollector |
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
