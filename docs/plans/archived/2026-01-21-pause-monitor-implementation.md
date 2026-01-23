# pause-monitor Redesign Implementation Plan

for design 2026-01-21-pause-monitor-redesign.md

---

## CRITICAL: Read This First (For AI Agents)

> **This is a PERSONAL PROJECT — one developer + AI assistants. NO external users. NO backwards compatibility.**

| Principle | What This Means | Anti-Pattern to AVOID |
|-----------|-----------------|----------------------|
| **Delete, don't deprecate** | If code is replaced, DELETE the old code | `@deprecated`, "kept for compatibility" |
| **No dead code** | Superseded code = DELETE it immediately | "might need later", commented-out code |
| **No stubs** | Implement it or don't include it | `return (0, 0)`, `pass`, `NotImplementedError` |
| **No migrations** | Schema changes? Delete the DB file, recreate fresh | `migrate_add_*()`, `ALTER TABLE` |
| **Breaking changes are FREE** | Change anything. No versioning needed. | `_v2` suffixes, compatibility shims |

**Implementation rule:** If old code conflicts with this plan → DELETE IT. If you see migration code → DELETE IT AND USE SCHEMA_VERSION CHECK INSTEAD.

**Database philosophy:** When schema changes, increment `SCHEMA_VERSION`. At startup, if version doesn't match, delete `data.db` and recreate. No migrations. Ever.

---

> **Sub-skill:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable real-time 10Hz TUI dashboard with complete 8-factor stress monitoring (including pageins), tier-appropriate forensics, and Unix socket streaming.

**Architecture:** Single 100ms loop driven by powermetrics. Ring buffer is the source of truth. Socket streams to TUI. SQLite stores only tier events (elevated bookmarks, pause forensics).

**Tech Stack:** Python 3.14, asyncio, Unix domain sockets, Textual TUI, SQLite (history only)

---

## Pre-Implementation Cleanup

Before beginning implementation, dead code was removed from the codebase. This cleanup is necessary to keep the refactor clean and avoid carrying dead weight.

### Cleanup Step 1 (Completed 2026-01-22)

The codebase had evolved from a SamplePolicy-based architecture to a Sentinel-based architecture, but the old code was left in place "for backwards compatibility." Since we have no external users, this dead code was removed.

**From `collector.py`:**
- `SamplePolicy` class
- `SamplingState` enum
- `PolicyResult` dataclass

**From `daemon.py`:**
- `_run_loop()`, `_collect_sample()`, `_check_for_pause()`, `_handle_pause()`, `_handle_policy_result()` methods
- `self.policy`, `self.pause_detector`, `self._powermetrics` fields
- Unused imports

**From tests:**
- 11 tests that exercised dead code paths

### Cleanup Step 2 (Completed 2026-01-22)

A simplification review identified additional dead code and unnecessary complexity:

**From `collector.py`:**
- `SystemMetrics` dataclass (unused)
- `get_system_metrics()` function (unused - Sentinel uses `collect_fast_metrics()`)
- `_get_io_counters()` stub (always returned `(0, 0)`)
- `_get_network_counters()` stub (always returned `(0, 0)`)
- Unused imports: `ctypes`, `subprocess`

**From `sentinel.py`:**
- `_slow_loop()` method (was a stub that only slept)
- `self.slow_interval` variable
- Simplified `start()` to directly await `_fast_loop()` instead of `asyncio.gather()`

**From tests:**
- `test_get_system_metrics_returns_complete` (testing deleted function)
- Removed `slow_interval` assertions from sentinel tests

**Result:** 263 tests pass, linter clean.

---

## Data Dictionary: powermetrics → Database Schema

This section documents the canonical data model. **powermetrics plist output is the source of truth.** All field names and types derive from it.

### Why powermetrics Is the Source of Truth

powermetrics is Apple's official tool for system telemetry. It provides:
- **Consistent structure**: Same plist format across macOS versions
- **Per-process attribution**: Identifies which process is consuming resources
- **Low overhead**: Designed for continuous monitoring
- **Privileged access**: Can see kernel-level data unavailable to user tools

Using powermetrics means we don't need psutil, IOKit bindings, or multiple data sources — everything comes from one authoritative stream.

### powermetrics Plist Structure

When run with `--samplers cpu_power,gpu_power,thermal,tasks,disk -f plist`, powermetrics outputs:

```
Top-level dict
├── is_delta: bool (always true for streaming)
├── elapsed_ns: integer (actual sample interval in nanoseconds)
├── timestamp: date (ISO 8601)
├── thermal_pressure: string ("Nominal"|"Moderate"|"Heavy"|"Critical"|"Sleeping")
├── tasks: array (per-process data)
│   └── [each task dict]
│       ├── pid: integer
│       ├── name: string
│       ├── cputime_ms_per_s: real (CPU usage: 1000 = 1 core fully used)
│       ├── intr_wakeups_per_s: real (interrupt-driven wakeups)
│       ├── idle_wakeups_per_s: real (idle wakeups — most relevant for energy)
│       ├── pageins_per_s: real (pages read from swap — KEY pause indicator)
│       ├── diskio_bytesread_per_s: real (per-process disk read)
│       ├── diskio_byteswritten_per_s: real (per-process disk write)
│       └── timer_wakeups: array
│           └── [{interval_ns, wakeups_per_s}, ...]
├── disk: dict (system-wide I/O)
│   ├── rbytes_per_s: real (read bytes/sec)
│   ├── wbytes_per_s: real (write bytes/sec)
│   ├── rops_per_s: real (read ops/sec)
│   └── wops_per_s: real (write ops/sec)
├── processor: dict
│   ├── clusters: array (per-cluster frequency/utilization)
│   ├── cpu_power: real (milliwatts)
│   ├── gpu_power: real (milliwatts)
│   └── combined_power: real (total SoC power in milliwatts)
└── gpu: dict
    ├── freq_hz: real
    ├── idle_ratio: real (1.0 = fully idle, 0.0 = fully busy)
    └── gpu_energy: integer (microjoules per interval)
```

### Reference Sample (from `powermetrics-sample.plist`)

A trimmed example showing the fields we extract. See `powermetrics-sample.plist` in project root for full output.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>is_delta</key><true/>
  <key>elapsed_ns</key><integer>1023685416</integer>
  <key>timestamp</key><date>2026-01-22T21:07:07Z</date>
  <key>thermal_pressure</key><string>Nominal</string>

  <key>tasks</key>
  <array>
    <dict>
      <key>pid</key><integer>55249</integer>
      <key>name</key><string>2.1.15</string>
      <key>cputime_ms_per_s</key><real>630.672</real>
      <key>idle_wakeups_per_s</key><real>0</real>
      <key>pageins_per_s</key><real>0</real>
      <key>diskio_bytesread_per_s</key><real>0</real>
      <key>diskio_byteswritten_per_s</key><real>16004.2</real>
    </dict>
    <!-- ... more tasks sorted by cputime_ms_per_s ... -->
  </array>

  <key>disk</key>
  <dict>
    <key>rbytes_per_s</key><real>56017.2</real>
    <key>wbytes_per_s</key><real>32009.8</real>
    <key>rops_per_s</key><real>12.6992</real>
    <key>wops_per_s</key><real>2.93059</real>
  </dict>

  <key>processor</key>
  <dict>
    <key>cpu_power</key><real>2978.45</real>
    <key>gpu_power</key><real>269.614</real>
    <key>combined_power</key><real>3248.07</real>
    <key>clusters</key><array><!-- per-CPU-cluster data --></array>
  </dict>

  <key>gpu</key>
  <dict>
    <key>freq_hz</key><real>338</real>
    <key>idle_ratio</key><real>0.877343</real>
    <key>gpu_energy</key><integer>284</integer>
  </dict>
</dict>
</plist>
```

### Field Mapping: powermetrics → PowermetricsResult

| powermetrics key | PowermetricsResult field | Type | Transform | Why |
|------------------|--------------------------|------|-----------|-----|
| `elapsed_ns` | `elapsed_ns` | int | direct | Actual interval for latency ratio calculation |
| `thermal_pressure` | `throttled` | bool | `!= "Nominal"` | Simplify to throttled/not-throttled for stress scoring |
| `processor.cpu_power` | `cpu_power` | float | direct | Power indicates load better than frequency |
| `processor.combined_power` | `combined_power` | float | direct | Total SoC power for trend analysis |
| `gpu.idle_ratio` | `gpu_pct` | float | `(1 - idle_ratio) * 100` | Convert to familiar percentage |
| `processor.gpu_power` | `gpu_power` | float | direct | Power indicates GPU work better than frequency |
| `disk.rbytes_per_s` | `io_read_per_s` | float | direct | Keep read/write separate for culprit ID |
| `disk.wbytes_per_s` | `io_write_per_s` | float | direct | Write-heavy vs read-heavy workloads differ |
| `tasks` | `top_cpu_processes` | list | Top 5 by cputime_ms_per_s | CPU culprit identification |
| `tasks` | `top_pagein_processes` | list | Top 5 by pageins_per_s | Memory pressure culprit identification |
| Sum of `tasks[].idle_wakeups_per_s` | `wakeups_per_s` | float | sum all tasks | System-wide wakeup rate |
| Sum of `tasks[].pageins_per_s` | `pageins_per_s` | float | sum all tasks | **Critical** — system-wide swap activity |

### Field Mapping: PowermetricsResult → Database (samples table)

| PowermetricsResult | samples column | Type | Why stored |
|--------------------|----------------|------|------------|
| timestamp | timestamp | REAL | Index for time-based queries |
| (computed) | interval | REAL | `elapsed_ns / 1e9` — for pause detection |
| (from stress) | stress_total | INTEGER | Primary metric for alerting |
| (from stress) | stress_load | INTEGER | Decomposed for trend analysis |
| (from stress) | stress_memory | INTEGER | (memory from `sysctl kern.memorystatus_level`) |
| (from stress) | stress_thermal | INTEGER | Contribution from throttled state |
| (from stress) | stress_latency | INTEGER | Contribution from interval deviation |
| (from stress) | stress_io | INTEGER | Contribution from I/O spikes |
| (from stress) | stress_gpu | INTEGER | Contribution from GPU saturation |
| (from stress) | stress_wakeups | INTEGER | Contribution from excessive wakeups |
| (from stress) | stress_pageins | INTEGER | **Critical** — contribution from swap activity |
| cpu_power | cpu_power | REAL | Power trend analysis |
| gpu_pct | gpu_pct | REAL | For historical charts |
| io_read_per_s | io_read_per_s | REAL | For I/O trend analysis |
| io_write_per_s | io_write_per_s | REAL | For I/O trend analysis |
| throttled | throttled | INTEGER | 0/1 for thermal tracking |
| wakeups_per_s | wakeups_per_s | REAL | For wakeup trend analysis |
| pageins_per_s | pageins_per_s | REAL | **Critical** — for memory pressure analysis |

**Note:** Some metrics come from sysctl, not powermetrics:

| Metric | Source | Notes |
|--------|--------|-------|
| `load_avg` | `os.getloadavg()[0]` | 1-minute load average |
| `mem_pressure` | `sysctl kern.memorystatus_level` | 0-100 scale (100 = no pressure, invert for stress) |
| `swap_used` | `sysctl vm.swapusage` | Bytes of swap in use |

**Why both `pageins_per_s` and `mem_pressure`?** They measure different things:
- `pageins_per_s` = rate of swap reads (active thrashing RIGHT NOW)
- `mem_pressure` = system memory state (predicts FUTURE thrashing)

### Design Decisions with Rationale

| Decision | Rationale |
|----------|-----------|
| **`pageins_per_s` is critical for pause detection** | Page-ins mean reading from swap — THE primary cause of user-visible pauses. A process doing 100+ pageins/sec will cause hangs. |
| **Track top processes by BOTH CPU and pageins** | CPU hogs ≠ memory hogs. A process using 5% CPU but thrashing swap is worse than one using 50% CPU with no pageins. |
| **Use `idle_wakeups_per_s` not `intr_wakeups_per_s`** | Idle wakeups indicate energy impact. Note: often 0 on Apple Silicon; may revisit if data shows `intr_wakeups` is more useful. |
| **Store rates, not cumulative values** | powermetrics already computes rates. Storing rates means samples are directly comparable regardless of interval length. |
| **Keep `io_read` and `io_write` separate** | Distinguishes read-heavy (database queries) from write-heavy (logging, backups) workloads. Combined I/O obscures the cause. |
| **Sum wakeups/pageins across all processes** | Individual process values matter for culprit ID, but system-wide totals indicate overall pressure. |
| **Use `thermal_pressure` string, not temp** | Apple silicon doesn't expose CPU temperature via powermetrics. Thermal pressure is the actionable signal. |
| **`gpu_pct` from `1 - idle_ratio`** | GPU "busy" is complement of idle. 96% idle = 4% busy. More intuitive as percentage. |
| **Top 5 processes per category** | Reduced from 10. Captures culprits without excessive storage. Two lists (CPU + pageins) = 10 total. |
| **`elapsed_ns` for latency calculation** | The actual interval lets us detect pauses: if we asked for 100ms but got 500ms, something blocked. |

### Schema Changes Required

Current schema has:
```sql
io_read   INTEGER,  -- ambiguous: bytes? bytes/sec?
io_write  INTEGER,  -- from _get_io_counters() stub (always returns 0!)
```

Should become:
```sql
io_read_per_s   REAL,   -- bytes/sec from powermetrics disk.rbytes_per_s
io_write_per_s  REAL,   -- bytes/sec from powermetrics disk.wbytes_per_s
wakeups_per_s   REAL,   -- sum of tasks[].idle_wakeups_per_s
pageins_per_s   REAL,   -- sum of tasks[].pageins_per_s (CRITICAL for pause detection)
cpu_power       REAL,   -- milliwatts from processor.cpu_power
gpu_power       REAL,   -- milliwatts from processor.gpu_power
stress_pageins  INTEGER -- contribution from swap activity
```

### Code Cleanup Required ✅ (Completed in Cleanup Step 2)

**Remove from `collector.py`:**
- `_get_io_counters()` stub — I/O now comes from powermetrics `disk` dict
- `_get_network_counters()` stub — network metrics not used in stress calculation

**Update `SystemMetrics` dataclass:**
- Remove `io_read`, `io_write`, `net_sent`, `net_recv` fields
- These were always 0 due to stub functions

**Update `get_system_metrics()`:**
- Remove calls to `_get_io_counters()` and `_get_network_counters()`
- Keep `load_avg`, `mem_available`, `swap_used` (not from powermetrics)

This is addressed in Phase 1 tasks.

---

## Phase 1: Unified Data Model Foundation — COMPLETE ✅

Completed 2026-01-22. All 6 tasks implemented and tested (127 tests pass).

See git history for implementation details.

---

## Phase 2: Update PowermetricsStream for 100ms + Complete Data — COMPLETE ✅

Completed 2026-01-22. All 4 tasks implemented and tested.

See git history for implementation details.

---

## Phase 3: Refactor Daemon as Single Loop — COMPLETE ✅

Completed 2026-01-23. All 7 tasks implemented and tested.

**Key changes:**
- Added `TierAction` enum and `TierManager` for tier state transitions
- Implemented 8-factor stress calculation directly from powermetrics data
- Added `peak_stress` column to events table (schema version 5)
- Created `_main_loop()` processing powermetrics at 10Hz
- Integrated tier action handling with pause detection and forensics
- Added peak tracking during elevated/critical tiers

See git history for implementation details.

---

## Phase 4: Add Socket Server

**Extracted to:** [`phase-4-socket-server.md`](phase-4-socket-server.md)

2 tasks adding Unix domain socket server for real-time TUI streaming:
- Task 4.1: Create SocketServer class (push-based design)
- Task 4.2: Integrate SocketServer into Daemon

---

## Phase 5: Update TUI to Use Socket

**Extracted to:** [`phase-5-socket-client-tui.md`](phase-5-socket-client-tui.md)

2 tasks adding Unix socket client and TUI integration:
- Task 5.1: Create SocketClient class (simple, stateless)
- Task 5.2: Update TUI to connect via socket

---

## Phase 6: Cleanup

### Task 6.1: Delete Sentinel Class, Keep Only TierManager

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Modify: `tests/test_sentinel.py`
- Modify: Any files importing `Sentinel`

**Step 1: Delete Sentinel class entirely**

Update `src/pause_monitor/sentinel.py`:

1. **Keep these (they're still needed):**
   - `Tier` enum
   - `TierAction` enum  
   - `TierManager` class

2. **DELETE entirely:**
   - `Sentinel` class (all of it)
   - `collect_fast_metrics()` function
   - Any imports only used by deleted code

The file should contain only `Tier`, `TierAction`, and `TierManager`.

**Step 2: Update imports throughout codebase**

Search for `from pause_monitor.sentinel import Sentinel` and remove. The Daemon creates its own TierManager directly.

**Step 2a: Remove IOBaselineManager from daemon.py**

The daemon currently imports and uses `IOBaselineManager` for I/O spike detection:

```python
# DELETE this import
from pause_monitor.stress import IOBaselineManager

# DELETE this line in __init__
self.io_baseline = IOBaselineManager(persisted_baseline=None)
```

The new `_calculate_stress()` method handles I/O directly from powermetrics data, so `IOBaselineManager` is no longer needed.

**Step 3: Delete Sentinel tests**

Update `tests/test_sentinel.py`:
- DELETE all tests that reference `Sentinel` class
- KEEP tests for `TierManager` and `TierAction`
- Rename file to `tests/test_tier_manager.py` if appropriate

**Step 4: Run tests**

Run: `uv run pytest -v`
Expected: All PASS (no references to deleted Sentinel)

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/
git commit -m "refactor: delete Sentinel class, keep TierManager only"
```

---

### Task 6.1.5: Delete Old Stress Functions

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Modify: `tests/test_stress.py`

**Rationale:** The old `calculate_stress()` function and `IOBaselineManager` class are now orphaned. The new architecture uses `Daemon._calculate_stress()` which computes all 8 factors directly from powermetrics data.

**Step 1: Delete from stress.py**

Delete entirely:
- `calculate_stress()` function
- `IOBaselineManager` class
- Any imports only used by deleted code

Keep:
- `StressBreakdown` dataclass (used throughout)
- `MemoryPressureLevel` enum (used by Daemon)
- `get_memory_pressure_fast()` function (used by Daemon)

**Step 2: Delete orphaned tests**

Delete from `tests/test_stress.py`:
- All tests that call `calculate_stress()` directly (approximately 13 tests)
- All tests for `IOBaselineManager`

Keep:
- Tests for `StressBreakdown.total` property
- Tests for `get_memory_pressure_fast()`

**Step 3: Run tests**

```bash
uv run pytest tests/test_stress.py -v
```

**Step 4: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "refactor: delete orphaned calculate_stress() and IOBaselineManager"
```

---

### Task 6.2: Remove SamplePolicy and slow_interval_ms ✅ COMPLETED

> **Already done in Cleanup Step 2 (Pre-Implementation).** See "Pre-Implementation Cleanup" section above.
>
> Removed: `SamplePolicy`, `SamplingState`, `PolicyResult`, `_get_io_counters`, `_get_network_counters`, `slow_interval_ms` config.

---

### Task 6.3: Remove Old Daemon._run_loop ✅ COMPLETED

> **Already done in Cleanup Step 1 (Pre-Implementation).** See "Pre-Implementation Cleanup" section above.
>
> Removed: `_run_loop()`, `_collect_sample()`, `_check_for_pause()`, `_handle_pause()`, `_handle_policy_result()`.

---

### Task 6.4: Update Memories

**Files:**
- Modify: `.serena/memories/unimplemented_features.md`
- Modify: `.serena/memories/implementation_guide.md`

**Step 1: Update unimplemented_features**

Mark as completed:
```markdown
## Completed (via Redesign)

- ~~Sentinel slow loop~~ → Replaced by Daemon powermetrics integration
- ~~TUI socket streaming~~ → Implemented via SocketServer/SocketClient
- ~~Complete 8-factor stress (including pageins)~~ → All factors now calculated from powermetrics
- ~~Process attribution~~ → Using powermetrics top_cpu_processes + top_pagein_processes
```

**Step 2: Update implementation_guide**

Document the new architecture:
```markdown
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
- `SamplePolicy` - Deleted
- `slow_interval_ms` config - Deleted
```

**Step 3: Commit**

```bash
git add .serena/memories/
git commit -m "docs: update memories after redesign"
```

---

## Verification Checklist

After completing all tasks:

1. **Daemon runs at 10Hz with complete stress**
   ```bash
   sudo uv run pause-monitor daemon
   # Check logs show samples every ~100ms with GPU, wakeups values
   ```

2. **Socket exists when daemon runs**
   ```bash
   ls -la ~/.local/share/pause-monitor/daemon.sock
   ```

3. **TUI shows "(live)" and updates at 10Hz**
   ```bash
   uv run pause-monitor tui
   # Subtitle should say "System Health Monitor (live)"
   # Stress values should update rapidly
   ```

4. **Tier 2 events create bookmarks with peak_stress**
   ```bash
   # Generate stress, wait for tier 2 exit
   uv run pause-monitor events
   # Should show recent event with peak stress
   ```

5. **All tests pass**
   ```bash
   uv run pytest -v
   ```

6. **Lint passes**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   ```

---

## Summary

| Phase | Tasks | Purpose |
|-------|-------|---------|
| 1 | 1.1–1.9 | Unified Data Model Foundation |
| 2 | 2.1–2.4 | Update PowermetricsStream for 100ms + complete data + failure handling + config |
| 3 | 3.1–3.7 | Refactor Daemon as single loop with tier handling, incident linking, peak tracking |
| 4 | 4.1–4.2 | Add Unix socket server |
| 5 | 5.1–5.2 | Update TUI to use socket client |
| 6 | 6.1–6.4 | Delete orphaned code, update docs |

**Total: 27 tasks** (Tasks 6.2–6.3 already completed in Pre-Implementation Cleanup)

**Key Architecture Changes:**
- powermetrics drives the main loop at 100ms (was 1000ms separate from sentinel)
- Ring buffer receives complete samples (was partial fast-path data)
- TUI streams from socket (was polling SQLite)
- SQLite stores only tier events (was storing all samples)
- Sentinel class deleted, TierManager used directly by Daemon
- Time-based correlation for related events (no explicit incident_id)
- Peak tracking every 30 seconds during elevated/critical periods
- Config-driven thresholds (pause_threshold_ratio, peak_tracking_seconds)
