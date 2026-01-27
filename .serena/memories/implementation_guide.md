# Implementation Guide

> **Phase 7 COMPLETE (2026-01-24).** Per-process scoring; SCHEMA_VERSION=7.

**Last updated:** 2026-01-25

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
|   TierManager    |-----------------+------------------>|   SocketServer   |
|  (sentinel.py)   |                                     | (socket_server)  |
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
- Methods: `to_dict()`, `from_dict()`

**ProcessSamples** — Collection from one sample:
- `timestamp`, `elapsed_ms`, `process_count`
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

Score = sum(normalized × weight), capped at 100, then multiplied by state multiplier.

---

## Module: daemon.py

**Purpose:** Main daemon orchestrating monitoring loop.

### DaemonState

Tracks runtime state:
- `running`, `sample_count`, `event_count`
- `last_sample_time`, `current_score` (max_score from ProcessSamples)
- `elevated_since`, `critical_since`

### Daemon

**Key attributes:**
- `collector` — TopCollector
- `ring_buffer` — RingBuffer
- `tier_manager` — TierManager
- `_socket_server` — SocketServer
- `_current_event_id` — Active escalation event

**Main loop flow:**
```python
while not shutdown:
    samples = await collector.collect()
    ring_buffer.push(samples, tier)
    action = tier_manager.update(samples.max_score)
    if action:
        await _handle_tier_action(action, samples)
    if tier == 3 and action != TIER3_ENTRY:
        _save_event_sample(samples, tier=3)
    if samples.elapsed_ms > expected * threshold:
        await _handle_pause(...)
    await socket_server.broadcast(samples, tier)
```

---

## Module: sentinel.py

**Purpose:** Tier state machine.

### Enums

- `Tier`: SENTINEL(1), ELEVATED(2), CRITICAL(3)
- `TierAction`: TIER2_ENTRY, TIER2_EXIT, TIER2_PEAK, TIER3_ENTRY, TIER3_EXIT

### TierManager

Manages transitions with 5s hysteresis for de-escalation.

**Thresholds (configurable):**
- elevated_threshold = 50
- critical_threshold = 75

**Key behavior:**
- Uses `max_score` from ProcessSamples for decisions
- TIER2_PEAK only emitted when new peak reached (not equal)
- Returns TierAction or None

---

## Module: storage.py

**Purpose:** SQLite with WAL mode.

### Constants
- `SCHEMA_VERSION = 7`
- `VALID_EVENT_STATUSES = {"unreviewed", "reviewed", "pinned", "dismissed"}`

### Tables (v7)

**events** — One row per escalation:
- `id`, `start_timestamp`, `end_timestamp`
- `peak_stress` (stores max_score), `peak_tier`
- `status`, `notes`

**process_sample_records** — ProcessSamples as JSON:
- `id`, `event_id`, `tier`
- `data` (TEXT JSON blob)

**Legacy (unused):** `event_samples`, `samples`, `process_samples`

### Dataclasses
- `Event` — Escalation event
- `ProcessSampleRecord` — Sample with ProcessSamples data

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

**TiersConfig:**
- elevated_threshold = 50
- critical_threshold = 75

### Config Paths
- config: `~/.config/pause-monitor/config.toml`
- data: `~/.local/share/pause-monitor/`
- db: `~/.local/share/pause-monitor/data.db`
- socket: `~/.local/share/pause-monitor/daemon.sock`

---

## Module: ringbuffer.py

**Purpose:** Fixed-size circular buffer.

### Classes
- `RingSample` — ProcessSamples + tier
- `BufferContents` — Immutable snapshot
- `RingBuffer` — deque(maxlen=30)

Methods: `push()`, `freeze()`, `clear()`

---

## Module: socket_server.py / socket_client.py

**Purpose:** Real-time TUI streaming.

### Protocol
Newline-delimited JSON over Unix socket.

**Message types:**
- `initial_state` — Ring buffer state on connect
- `sample` — ProcessSamples data per sample

**SocketServer methods:**
- `start()`, `stop()`
- `broadcast(samples, tier)` — Push to all clients
- `has_clients` property — For main loop optimization

---

## Module: forensics.py

**Purpose:** Diagnostic capture on pause.

### ForensicsCapture
- `write_metadata()`, `write_process_snapshot()`
- `write_ring_buffer()` — Serializes ProcessSamples

### Functions
- `capture_spindump()`, `capture_tailspin()`, `capture_system_logs()`
- `identify_culprits(contents)` — Top rogues from buffer

---

## Module: tui/app.py

**Purpose:** Textual dashboard.

### Widgets
- `StressGauge` — Score meter (max_score)
- `SampleInfoPanel` — Tier, counts
- `ProcessesPanel` — Rogue process table
- `EventsTable` — Recent events

### Screens
- `EventsScreen` — Full event list with filtering
- `EventDetailScreen` — Single event details

### Data Flow
- Real-time: SocketClient → `_handle_socket_data()`
- Events: SQLite → `_refresh_events()`

---

## Deleted Components

These no longer exist:
- `stress.py` — Replaced by per-process scoring
- `StressBreakdown` — Use ProcessScore.score
- `PowermetricsStream` — Use TopCollector
- `Sentinel` class — Use TierManager directly
- `calculate_stress()` — Deleted
- `IOBaselineManager` — Deleted

---

## Testing

| File | Coverage |
|------|----------|
| test_collector.py | TopCollector parsing, scoring |
| test_daemon.py | Daemon state, main loop |
| test_tier_manager.py | TierManager state machine |
| test_storage.py | Database operations |
| test_ringbuffer.py | Ring buffer operations |
| test_socket_*.py | Socket server/client |
| test_forensics.py | Forensics capture |

Run: `uv run pytest`
