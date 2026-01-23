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

## Phase 3: Refactor Daemon as Single Loop

**Note:** Tasks are ordered to resolve dependencies. Each task builds on the previous.

### Task 3.1: Add TierAction Enum and TierManager to Daemon

**Files:**
- Modify: `src/pause_monitor/sentinel.py` (add TierAction enum)
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Add TierAction enum to sentinel.py**

Add this enum after the existing `Tier` enum in `src/pause_monitor/sentinel.py`:
```python
from enum import IntEnum, StrEnum

class TierAction(StrEnum):
    """Actions returned by TierManager on state transitions."""
    TIER2_ENTRY = "tier2_entry"
    TIER2_EXIT = "tier2_exit"
    TIER2_PEAK = "tier2_peak"
    TIER3_ENTRY = "tier3_entry"
    TIER3_EXIT = "tier3_exit"
```

**Step 2: Update TierManager.update() return type**

Change the method signature and returns in `TierManager.update()`:
```python
def update(self, stress_total: int) -> TierAction | None:
    """Update tier state based on current stress.

    Returns TierAction if state change occurred, None otherwise.
    """
    # ... existing logic, but replace string returns:
    # return "tier3_entry"  ->  return TierAction.TIER3_ENTRY
    # return "tier2_entry"  ->  return TierAction.TIER2_ENTRY
    # action = "tier2_peak" ->  action = TierAction.TIER2_PEAK
    # return "tier3_exit"   ->  return TierAction.TIER3_EXIT
    # return "tier2_exit"   ->  return TierAction.TIER2_EXIT
```

**Step 3: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

def test_daemon_has_tier_manager(tmp_path):
    """Daemon should have TierManager for tier transitions."""
    config = Config()
    config._data_dir = tmp_path
    daemon = Daemon(config)

    assert hasattr(daemon, "tier_manager")
    # current_tier returns int directly, not Tier enum
    assert daemon.tier_manager.current_tier == 1  # SENTINEL
```

**Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_has_tier_manager -v`
Expected: FAIL (Daemon has no tier_manager attribute directly)

**Step 5: Write minimal implementation**

Add to `Daemon.__init__` in `src/pause_monitor/daemon.py`:
```python
from pause_monitor.sentinel import TierManager, TierAction

# Tier management (replaces sentinel.tier_manager)
self.tier_manager = TierManager(
    elevated_threshold=config.tiers.elevated_threshold,
    critical_threshold=config.tiers.critical_threshold,
)
```

**Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_has_tier_manager -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/pause_monitor/sentinel.py src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add TierAction enum and TierManager"
```

---

### Task 3.2: Add Stress Calculation Method to Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

from pause_monitor.collector import PowermetricsResult


def test_daemon_calculate_stress_all_factors(tmp_path):
    """Daemon should calculate stress with all 8 factors from powermetrics."""
    config = Config()
    config._data_dir = tmp_path
    daemon = Daemon(config)

    # Phase 1 updated PowermetricsResult - uses Data Dictionary fields
    pm_result = PowermetricsResult(
        elapsed_ns=100_000_000,
        throttled=True,
        cpu_power=15.0,
        gpu_pct=90.0,
        gpu_power=8.0,
        io_read_per_s=30_000_000.0,  # 30 MB/s read
        io_write_per_s=20_000_000.0,  # 20 MB/s write = 50 MB/s total
        wakeups_per_s=300.0,
        pageins_per_s=50.0,  # Some swap activity
        top_cpu_processes=[{"name": "test", "pid": 123, "cpu_ms_per_s": 500.0}],
        top_pagein_processes=[{"name": "swapper", "pid": 456, "pageins_per_s": 50.0}],
    )

    stress = daemon._calculate_stress(pm_result, latency_ratio=1.5)

    # Verify all factors are calculated
    assert stress.load >= 0  # Based on system load
    assert stress.memory >= 0
    assert stress.thermal == 10  # throttled = 10 points
    assert stress.latency > 0  # latency_ratio 1.5 should contribute
    assert stress.gpu > 0  # 90% GPU
    assert stress.wakeups > 0  # 300 wakeups/sec
    assert stress.io > 0  # 50 MB/s should contribute
    assert stress.pageins > 0  # 50 pageins/sec should contribute
    assert stress.total > 0  # Total should be sum of all 8 factors
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_calculate_stress_all_factors -v`
Expected: FAIL (no _calculate_stress method)

**Step 3: Write minimal implementation**

Add imports at top of `src/pause_monitor/daemon.py`:
```python
import os
from pause_monitor.collector import PowermetricsResult
from pause_monitor.stress import StressBreakdown, get_memory_pressure_fast
```

Add to `Daemon.__init__` (after existing attributes):
```python
self.core_count = os.cpu_count() or 1
```

Add method to `Daemon` class:
```python
def _calculate_stress(
    self, pm_result: PowermetricsResult, latency_ratio: float
) -> StressBreakdown:
    """Calculate stress breakdown from powermetrics data.

    Args:
        pm_result: Parsed powermetrics sample
        latency_ratio: Actual interval / expected interval (1.0 = on time)

    Returns:
        StressBreakdown with all 8 factors (including pageins - critical for pause detection)
    """
    # Get system metrics
    load_avg = os.getloadavg()[0]
    mem_pressure = get_memory_pressure_fast()

    # Load stress (0-30 points)
    load_ratio = load_avg / self.core_count if self.core_count > 0 else 0
    if load_ratio < 1.0:
        load = 0
    elif load_ratio < 2.0:
        load = int((load_ratio - 1.0) * 15)  # 0-15 for 1x-2x
    else:
        load = int(min(30, 15 + (load_ratio - 2.0) * 7.5))  # 15-30 for 2x+

    # Memory stress (0-30 points) - higher pressure = more stress
    if mem_pressure < 20:
        memory = 0
    elif mem_pressure < 50:
        memory = int((mem_pressure - 20) * 0.5)  # 0-15 for 20-50%
    else:
        memory = int(min(30, 15 + (mem_pressure - 50) * 0.3))  # 15-30 for 50%+

    # Thermal stress (0-10 points)
    thermal = 10 if pm_result.throttled else 0

    # Latency stress (0-20 points) - uses config threshold
    pause_threshold = self.config.sentinel.pause_threshold_ratio
    if latency_ratio <= 1.2:
        latency = 0
    elif latency_ratio <= pause_threshold:
        # Scale from 0-10 between 1.2x and threshold
        latency = int((latency_ratio - 1.2) / (pause_threshold - 1.2) * 10)
    else:
        # 10-20 for ratios above threshold
        latency = int(min(20, 10 + (latency_ratio - pause_threshold) * 5))

    # GPU stress (0-20 points)
    gpu = 0
    if pm_result.gpu_pct is not None:
        if pm_result.gpu_pct > 80:
            gpu = int(min(20, (pm_result.gpu_pct - 80) * 1.0))  # 0-20 for 80-100%
        elif pm_result.gpu_pct > 50:
            gpu = int((pm_result.gpu_pct - 50) * 0.33)  # 0-10 for 50-80%

    # Wakeups stress (0-10 points)
    wakeups = 0
    if pm_result.wakeups_per_s > 100:
        wakeups = int(min(10, (pm_result.wakeups_per_s - 100) / 40))  # 100-500 -> 0-10

    # I/O stress (0-10 points)
    # Scale: 0-10 MB/s = 0, 10-100 MB/s = 0-10 points
    # Per Data Dictionary: use io_read_per_s + io_write_per_s
    io = 0
    io_mb_per_sec = (pm_result.io_read_per_s + pm_result.io_write_per_s) / (1024 * 1024)
    if io_mb_per_sec > 10:
        io = int(min(10, (io_mb_per_sec - 10) / 9))  # 10-100 MB/s -> 0-10

    # Pageins stress (0-30 points) - CRITICAL for pause detection
    # Scale: 0-10 pageins/s = 0, 10-100 = 0-15, 100+ = 15-30
    # This is the #1 indicator of user-visible pauses
    pageins = 0
    if pm_result.pageins_per_s > 10:
        if pm_result.pageins_per_s < 100:
            pageins = int((pm_result.pageins_per_s - 10) / 6)  # 10-100 -> 0-15
        else:
            pageins = int(min(30, 15 + (pm_result.pageins_per_s - 100) / 20))  # 100+ -> 15-30

    return StressBreakdown(
        load=load,
        memory=memory,
        thermal=thermal,
        latency=latency,
        io=io,
        gpu=gpu,
        wakeups=wakeups,
        pageins=pageins,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_calculate_stress_all_factors -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add stress calculation with all 8 factors"
```

---

### Task 3.3a: Add peak_stress to Storage Schema

> **SIMPLIFIED:** Removed `incident_id` field. The original design used incident_id to link related events (escalation + recovery), but time-based correlation is simpler and sufficient — events within a short window are naturally related. This removes significant complexity from the daemon tier tracking (7 instance variables reduced to 4).

> **Note:** No migration function needed. This is a personal dev project — if schema changes break an existing database, just delete `~/.local/share/pause-monitor/data.db` and let it recreate fresh.

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
# tests/test_storage.py - add to existing file

def test_event_has_peak_stress(tmp_path):
    """Event should support peak_stress field."""
    from pause_monitor.storage import Event, insert_event, get_events, init_database
    from pause_monitor.stress import StressBreakdown
    from datetime import datetime
    import sqlite3

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = sqlite3.connect(db_path)

    event = Event(
        timestamp=datetime.now(),
        duration=60.0,
        stress=StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=5, wakeups=1, pageins=0),
        culprits=["test_app"],
        event_dir=None,
        peak_stress=35,
    )

    event_id = insert_event(conn, event)
    assert event_id > 0

    events = get_events(conn, limit=1)
    assert len(events) == 1
    assert events[0].peak_stress == 35

    conn.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_event_has_peak_stress -v`
Expected: FAIL (Event has no peak_stress field)

**Step 3: Update the `Event` dataclass** in `src/pause_monitor/storage.py`:
```python
@dataclass
class Event:
    """Pause event record."""

    timestamp: datetime
    duration: float
    stress: StressBreakdown
    culprits: list[str]
    event_dir: str | None
    status: str = "unreviewed"  # unreviewed, reviewed, pinned, dismissed
    notes: str | None = None
    id: int | None = None
    peak_stress: int | None = None  # Peak stress during this event
```

**Step 4: Update the SCHEMA** - add peak_stress column to the `events` table in the SCHEMA string:
```sql
-- Pause events
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    timestamp       REAL NOT NULL,
    duration        REAL NOT NULL,
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER,
    stress_gpu      INTEGER,
    stress_wakeups  INTEGER,
    culprits        TEXT,
    event_dir       TEXT,
    status          TEXT DEFAULT 'unreviewed',
    notes           TEXT,
    peak_stress     INTEGER
);
```

**Step 5: Update `insert_event`** function:
```python
def insert_event(conn: sqlite3.Connection, event: Event) -> int:
    """Insert an event and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO events (
            timestamp, duration, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io, stress_gpu, stress_wakeups,
            culprits, event_dir, status, notes, peak_stress
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.timestamp.timestamp(),
            event.duration,
            event.stress.total,
            event.stress.load,
            event.stress.memory,
            event.stress.thermal,
            event.stress.latency,
            event.stress.io,
            event.stress.gpu,
            event.stress.wakeups,
            json.dumps(event.culprits),
            event.event_dir,
            event.status,
            event.notes,
            event.peak_stress,
        ),
    )
    conn.commit()
    return cursor.lastrowid
```

**Step 6: Update `get_events`** function:
```python
def get_events(
    conn: sqlite3.Connection,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    status: str | None = None,
) -> list[Event]:
    """Get events, optionally filtered by time range and/or status."""
    query = """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, stress_gpu, stress_wakeups,
               culprits, event_dir, status, notes, peak_stress
        FROM events
    """
    params: list = []
    conditions = []

    if start:
        conditions.append("timestamp >= ?")
        params.append(start.timestamp())
    if end:
        conditions.append("timestamp <= ?")
        params.append(end.timestamp())
    if status:
        conditions.append("status = ?")
        params.append(status)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        Event(
            id=row[0],
            timestamp=datetime.fromtimestamp(row[1]),
            duration=row[2],
            stress=StressBreakdown(
                load=row[4] or 0,
                memory=row[5] or 0,
                thermal=row[6] or 0,
                latency=row[7] or 0,
                io=row[8] or 0,
                gpu=row[9] or 0,
                wakeups=row[10] or 0,
            ),
            culprits=json.loads(row[11]) if row[11] else [],
            event_dir=row[12],
            status=row[13] or "unreviewed",
            notes=row[14],
            peak_stress=row[15],
        )
        for row in rows
    ]
```

**Step 7: Bump SCHEMA_VERSION** at top of file:
```python
SCHEMA_VERSION = 3  # Was 2, bump for peak_stress column
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py::test_event_has_peak_stress -v`
Expected: PASS

**Step 9: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add peak_stress to Event schema"
```

---

### Task 3.3b: Handle Tier Actions in Daemon with Incident Linking

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing tests**

```python
# tests/test_daemon.py - add to existing file

import time
import uuid
from pause_monitor.stress import StressBreakdown
from pause_monitor.storage import get_events


@pytest.mark.asyncio
async def test_daemon_handles_tier2_exit_writes_bookmark(tmp_path):
    """Daemon should write bookmark to DB on tier2_exit."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)
    await daemon._init_database()

    # Simulate tier 2 entry via TierManager (single source of truth)
    # First enter tier 2 to set entry time, then manually adjust for test
    daemon.tier_manager.update(20)  # Enter tier 2
    daemon.tier_manager._tier2_entry_time = time.monotonic() - 60  # Simulate 60s ago
    
    # Set peak stress tracking in daemon
    daemon._tier2_peak_stress = 35
    daemon._tier2_peak_breakdown = StressBreakdown(
        load=10, memory=8, thermal=5, latency=3, io=2, gpu=5, wakeups=2
    )

    # Handle tier2_exit (entry time comes from TierManager)
    from pause_monitor.sentinel import TierAction
    stress = StressBreakdown(load=5, memory=3, thermal=0, latency=0, io=0, gpu=2, wakeups=1, pageins=0)
    await daemon._handle_tier_action(TierAction.TIER2_EXIT, stress)

    # Verify event was written with peak_stress
    events = get_events(daemon._conn, limit=1)
    assert len(events) == 1
    assert events[0].peak_stress == 35
    assert events[0].duration >= 59  # Should be ~60s (with some tolerance)


# NOTE: test_daemon_tier3_to_tier2_links_incident removed — incident_id tracking
# was eliminated as YAGNI. Time-based correlation handles event linking.
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_tier2_exit_writes_bookmark -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add imports at top of `src/pause_monitor/daemon.py`:
```python
import uuid
from datetime import datetime, timedelta
from pause_monitor.storage import (
    Event, insert_event, init_database,
    # No migration imports — schema mismatches trigger delete+recreate
)
```

Add to `Daemon.__init__`:
```python
# Tier 2 peak tracking (entry time comes from TierManager per Design Simplification #5)
self._tier2_peak_stress: int = 0
self._tier2_peak_breakdown: StressBreakdown | None = None
self._tier2_peak_process: str | None = None

# Peak tracking timer
self._last_peak_check: float = 0.0
```

> **Note:** `_tier2_start_time` and `_tier3_start_time` are NOT added here.
> Per Design Simplification #5, TierManager is the single source of truth for entry times.
> Daemon reads `tier_manager.tier2_entry_time` and `tier_manager.tier3_entry_time` instead.

Add `_init_database` method to `Daemon` (extracted from `start()` for testability):
```python
async def _init_database(self) -> None:
    """Initialize database connection.

    Extracted from start() so tests can initialize DB without full daemon startup.
    No migrations — if schema version mismatches, init_database() deletes and recreates.
    """
    self.config.data_dir.mkdir(parents=True, exist_ok=True)
    init_database(self.config.db_path)  # Handles version check + recreate
    self._conn = sqlite3.connect(self.config.db_path)
```

Add `_handle_tier_action` method to `Daemon`:
```python
async def _handle_tier_action(self, action: TierAction, stress: StressBreakdown) -> None:
    """Handle tier transition actions.
    
    Entry times are read from TierManager (single source of truth per Design
    Simplification #5). TierManager uses time.monotonic() for stability, so we
    compute wall-clock entry time from duration when creating events.
    """
    if action == TierAction.TIER2_ENTRY:
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.info("tier2_entered", stress=stress.total)

    elif action == TierAction.TIER2_PEAK:
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.info("tier2_new_peak", stress=stress.total)

    elif action == TierAction.TIER2_EXIT:
        # Read entry time from TierManager (monotonic domain)
        entry_time = self.tier_manager.tier2_entry_time
        if entry_time is not None:
            duration = time.monotonic() - entry_time
            # Compute wall-clock entry time from duration
            entry_timestamp = datetime.now() - timedelta(seconds=duration)
            event = Event(
                timestamp=entry_timestamp,
                duration=duration,
                stress=self._tier2_peak_breakdown or stress,
                culprits=[],  # Populated from ring buffer snapshot
                event_dir=None,  # Bookmarks don't have forensics
                status="unreviewed",
                peak_stress=self._tier2_peak_stress,
            )
            insert_event(self._conn, event)
            self.state.event_count += 1
            log.info("tier2_exited", duration=duration, peak=self._tier2_peak_stress)

        self._tier2_peak_stress = 0
        self._tier2_peak_breakdown = None
        self.ring_buffer.clear_snapshots()

    elif action == TierAction.TIER3_ENTRY:
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.warning("tier3_entered", stress=stress.total)

    elif action == TierAction.TIER3_EXIT:
        # De-escalating to tier 2 - TierManager handles entry time tracking
        # Peak tracking starts fresh for recovery period
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        log.info("tier3_exited", stress=stress.total)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_tier2_exit_writes_bookmark -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): handle tier actions with peak stress tracking"
```

---

### Task 3.4: Handle Pause Detection in Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_daemon_handles_pause_runs_forensics(tmp_path, monkeypatch):
    """Daemon should run full forensics on pause detection."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)
    await daemon._init_database()

    # Track forensics calls
    forensics_called = []

    async def mock_run_forensics(contents):
        forensics_called.append(contents)

    monkeypatch.setattr(daemon, "_run_forensics", mock_run_forensics)

    # Mock sleep wake detection to return False (not a sleep wake)
    monkeypatch.setattr(
        "pause_monitor.daemon.was_recently_asleep",
        lambda within_seconds: False,
    )

    # Add some samples to ring buffer (Phase 1: push requires metrics)
    for i in range(5):
        metrics = PowermetricsResult(
            elapsed_ns=100_000_000,
            throttled=False,
            cpu_power=5.0 + i,
            gpu_pct=10.0,
            gpu_power=1.0,
            io_read_per_s=1000.0,
            io_write_per_s=500.0,
            wakeups_per_s=50.0,
            pageins_per_s=0.0,
            top_cpu_processes=[],
            top_pagein_processes=[],
        )
        stress = StressBreakdown(
            load=10 + i, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0, pageins=0
        )
        daemon.ring_buffer.push(metrics, stress, tier=1)

    # Handle pause (300ms actual when 100ms expected = 3x latency)
    await daemon._handle_pause(actual_interval=0.3, expected_interval=0.1)

    assert len(forensics_called) == 1
    # Forensics received frozen buffer contents
    assert len(forensics_called[0].samples) == 5
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_pause_runs_forensics -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add imports at top of `src/pause_monitor/daemon.py`:
```python
import asyncio
from pause_monitor.sleepwake import was_recently_asleep
from pause_monitor.forensics import ForensicsCapture, identify_culprits, run_full_capture
from pause_monitor.ringbuffer import BufferContents
```

Add methods to `Daemon`:
```python
async def _handle_pause(self, actual_interval: float, expected_interval: float) -> None:
    """Handle detected pause - run full forensics.

    A pause is when our loop was delayed >threshold (system was frozen).

    Args:
        actual_interval: How long the loop actually took
        expected_interval: How long it should have taken
    """
    # Check if we just woke from sleep (not a real pause)
    if was_recently_asleep(within_seconds=actual_interval):
        log.info("pause_was_sleep_wake", actual=actual_interval)
        return

    log.warning(
        "pause_detected",
        actual=actual_interval,
        expected=expected_interval,
        ratio=actual_interval / expected_interval,
    )

    # Freeze ring buffer (immutable snapshot)
    contents = self.ring_buffer.freeze()

    # Run forensics in background
    await self._run_forensics(contents)


async def _run_forensics(self, contents: BufferContents) -> None:
    """Run full forensics capture.

    Args:
        contents: Frozen ring buffer contents
    """
    # Create event directory
    timestamp = datetime.now()
    event_dir = self.config.events_dir / timestamp.strftime("%Y%m%d_%H%M%S")
    event_dir.mkdir(parents=True, exist_ok=True)

    # Identify culprits from ring buffer using powermetrics data
    # identify_culprits returns [{"factor": str, "score": int, "processes": [str]}]
    culprits = identify_culprits(contents)
    # Flatten process lists from top factors, dedupe, keep top 5
    all_procs = [p for c in culprits for p in c.get("processes", [])]
    culprit_names = list(dict.fromkeys(all_procs))[:5]

    # Create capture context
    capture = ForensicsCapture(event_dir)

    # Write ring buffer data
    capture.write_ring_buffer(contents)

    # Find peak sample
    peak_sample = (
        max(contents.samples, key=lambda s: s.stress.total) if contents.samples else None
    )
    peak_stress = peak_sample.stress.total if peak_sample else 0

    # Write metadata
    capture.write_metadata(
        {
            "timestamp": timestamp.isoformat(),
            "peak_stress": peak_stress,
            "culprits": culprit_names,
            "sample_count": len(contents.samples),
        }
    )

    # Run heavy captures (spindump, tailspin, logs) in background
    asyncio.create_task(
        run_full_capture(capture, window=self.config.sentinel.ring_buffer_seconds)
    )

    # Write event to database
    event = Event(
        timestamp=timestamp,
        duration=0.0,  # Pause duration unknown until next sample
        stress=peak_sample.stress if peak_sample else StressBreakdown(0, 0, 0, 0, 0, 0, 0, 0),
        culprits=culprit_names,
        event_dir=str(event_dir),
        status="unreviewed",
        peak_stress=peak_stress,
    )
    insert_event(self._conn, event)
    self.state.event_count += 1

    # Notify user
    self.notifier.pause_detected(duration=0, event_dir=event_dir)

    log.info("forensics_started", event_dir=str(event_dir), culprits=culprit_names)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_pause_runs_forensics -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): handle pause detection with full forensics"
```

---

### Task 3.5: Add Peak Tracking Timer

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

def test_daemon_updates_peak_after_interval(tmp_path):
    """Daemon should update peak stress after peak_tracking_seconds."""
    config = Config()
    config._data_dir = tmp_path
    config.sentinel.peak_tracking_seconds = 30

    daemon = Daemon(config)

    # Simulate being in tier 2 via TierManager (single source of truth)
    daemon.tier_manager.update(20)  # Enter tier 2
    daemon.tier_manager._tier2_entry_time = time.monotonic() - 60  # Simulate 60s ago
    daemon._tier2_peak_stress = 20
    daemon._last_peak_check = time.time() - 35  # 35 seconds ago

    # New stress is higher
    new_stress = StressBreakdown(load=15, memory=10, thermal=5, latency=3, io=2, gpu=5, wakeups=2, pageins=5)

    # Should update peak
    daemon._maybe_update_peak(new_stress)

    assert daemon._tier2_peak_stress == new_stress.total
    assert daemon._tier2_peak_breakdown == new_stress


def test_daemon_does_not_update_peak_before_interval(tmp_path):
    """Daemon should not update peak before peak_tracking_seconds."""
    config = Config()
    config._data_dir = tmp_path
    config.sentinel.peak_tracking_seconds = 30

    daemon = Daemon(config)

    # Simulate being in tier 2 via TierManager (single source of truth)
    daemon.tier_manager.update(50)  # Enter tier 2 with high stress
    daemon.tier_manager._tier2_entry_time = time.monotonic() - 60  # Simulate 60s ago
    daemon._tier2_peak_stress = 50
    daemon._last_peak_check = time.time() - 10  # Only 10 seconds ago

    # New stress is lower
    new_stress = StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=0, gpu=5, wakeups=0, pageins=0)

    # Should not update peak (not enough time passed)
    daemon._maybe_update_peak(new_stress)

    assert daemon._tier2_peak_stress == 50  # Unchanged
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py::test_daemon_updates_peak_after_interval tests/test_daemon.py::test_daemon_does_not_update_peak_before_interval -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add method to `Daemon`:
```python
def _maybe_update_peak(self, stress: StressBreakdown) -> None:
    """Update peak stress if interval has passed and stress is higher.

    This ensures long elevated/critical periods capture the worst moment
    before the ring buffer rolls over.

    Args:
        stress: Current stress breakdown
    """
    now = time.time()
    interval = self.config.sentinel.peak_tracking_seconds

    # Only check periodically
    if now - self._last_peak_check < interval:
        return

    self._last_peak_check = now

    # Update if current stress is higher
    if stress.total > self._tier2_peak_stress:
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        # Get top CPU process from latest powermetrics data
        if self._latest_pm_result and self._latest_pm_result.top_cpu_processes:
            self._tier2_peak_process = self._latest_pm_result.top_cpu_processes[0]["name"]
        # Also track top pagein process if any (more likely cause of pauses)
        if self._latest_pm_result and self._latest_pm_result.top_pagein_processes:
            self._tier2_peak_pagein_process = self._latest_pm_result.top_pagein_processes[0]["name"]
        self.ring_buffer.snapshot_processes(trigger="peak_update")
        log.info("peak_updated", stress=stress.total)
```

Also add to `Daemon.__init__`:
```python
self._latest_pm_result: PowermetricsResult | None = None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon.py::test_daemon_updates_peak_after_interval tests/test_daemon.py::test_daemon_does_not_update_peak_before_interval -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add periodic peak stress tracking"
```

---

### Task 3.6: Create Main Loop Method in Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_daemon_main_loop_processes_powermetrics(tmp_path, monkeypatch):
    """Daemon main loop should process powermetrics samples."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)

    # Track samples pushed to ring buffer (Phase 1: new signature)
    pushed_samples = []
    original_push = daemon.ring_buffer.push

    def track_push(metrics, stress, tier):
        pushed_samples.append((metrics, stress, tier))
        return original_push(metrics, stress, tier)

    monkeypatch.setattr(daemon.ring_buffer, "push", track_push)

    # Mock powermetrics to yield two samples then stop
    # Phase 1 updated PowermetricsResult - uses Data Dictionary fields
    samples = [
        PowermetricsResult(
            elapsed_ns=100_000_000,
            throttled=False,
            cpu_power=5.0,
            gpu_pct=30.0,
            gpu_power=2.0,
            io_read_per_s=0.0,
            io_write_per_s=0.0,
            wakeups_per_s=100.0,
            pageins_per_s=0.0,
            top_cpu_processes=[{"name": "test", "pid": 1, "cpu_ms_per_s": 100}],
            top_pagein_processes=[],
        ),
        PowermetricsResult(
            elapsed_ns=100_000_000,
            throttled=True,
            cpu_power=12.0,
            gpu_pct=60.0,
            gpu_power=5.0,
            io_read_per_s=0.0,
            io_write_per_s=0.0,
            wakeups_per_s=200.0,
            pageins_per_s=10.0,  # Some swap activity
            top_cpu_processes=[{"name": "test", "pid": 1, "cpu_ms_per_s": 200}],
            top_pagein_processes=[{"name": "swapper", "pid": 2, "pageins_per_s": 10.0}],
        ),
    ]

    async def mock_read_samples():
        for s in samples:
            yield s

    mock_stream = MagicMock()
    mock_stream.start = AsyncMock()
    mock_stream.stop = AsyncMock()
    mock_stream.read_samples = mock_read_samples
    daemon._powermetrics = mock_stream

    # Run main loop (will exit after samples exhausted)
    await daemon._main_loop()

    assert len(pushed_samples) == 2
    # Second sample had higher stress (throttled, high GPU)
    assert pushed_samples[1][0].thermal == 10  # throttled = 10 points
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_main_loop_processes_powermetrics -v`
Expected: FAIL (no _main_loop method)

**Step 3: Write minimal implementation**

Add to `Daemon.__init__` (after existing attributes):
```python
self._shutdown_event = asyncio.Event()
self._powermetrics: PowermetricsStream | None = None
self._latest_pm_result: PowermetricsResult | None = None
```

Add signal handler method to `Daemon`:
```python
def _handle_signal(self, sig: signal.Signals) -> None:
    """Handle shutdown signal by setting the shutdown event."""
    log.info("shutdown_signal_received", signal=sig.name)
    self._shutdown_event.set()
```

Add main loop method to `Daemon`:
```python
async def _main_loop(self) -> None:
    """Main loop: process powermetrics samples at 10Hz.

    Each sample:
    1. Measure latency (pause detection)
    2. Calculate stress from powermetrics data
    3. Push to ring buffer
    4. Update tier manager
    5. Handle tier transitions
    6. Periodic peak tracking

    If powermetrics crashes, restart it after 1 second.
    """
    expected_interval = self.config.sentinel.fast_interval_ms / 1000.0
    pause_threshold = self.config.sentinel.pause_threshold_ratio

    while not self._shutdown_event.is_set():
        # (Re)create powermetrics stream
        self._powermetrics = PowermetricsStream(
            interval_ms=self.config.sentinel.fast_interval_ms
        )

        try:
            await self._powermetrics.start()
            last_sample_time = time.monotonic()

            async for pm_result in self._powermetrics.read_samples():
                if self._shutdown_event.is_set():
                    break

                # Measure actual interval for latency/pause detection
                now = time.monotonic()
                actual_interval = now - last_sample_time
                last_sample_time = now
                latency_ratio = actual_interval / expected_interval

                # Store latest powermetrics result for peak tracking
                self._latest_pm_result = pm_result

                # Calculate stress from powermetrics data
                stress = self._calculate_stress(pm_result, latency_ratio)

                # Get current tier for the sample
                current_tier = self.tier_manager.current_tier

                # Push to ring buffer (Phase 1: includes raw metrics for forensics)
                self.ring_buffer.push(pm_result, stress, tier=current_tier)

                # Push to socket for TUI (push-based streaming per Design Simplifications)
                if self._socket_server and self._socket_server.has_clients:
                    await self._socket_server.broadcast(pm_result, stress, current_tier)

                # Update tier manager and handle transitions
                action = self.tier_manager.update(stress.total)
                if action:
                    await self._handle_tier_action(action, stress)

                # Periodic peak tracking during elevated/critical
                if current_tier >= 2:
                    self._maybe_update_peak(stress)

                # Check for pause (latency > threshold)
                if latency_ratio > pause_threshold:
                    await self._handle_pause(actual_interval, expected_interval)

                self.state.sample_count += 1

        except asyncio.CancelledError:
            log.info("main_loop_cancelled")
            break
        except Exception as e:
            log.error("powermetrics_crashed", error=str(e))
            await asyncio.sleep(1.0)  # Wait before restart
        finally:
            if self._powermetrics:
                await self._powermetrics.stop()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_main_loop_processes_powermetrics -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add main loop processing powermetrics at 10Hz"
```

---

### Task 3.7: Update Daemon.start() to Use Main Loop

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Update start() method**

Replace the section that calls `sentinel.start()` with `_main_loop()`:

```python
async def start(self) -> None:
    """Start the daemon."""
    log.info("daemon_starting")

    # Set QoS to USER_INITIATED for reliable sampling under load
    # This ensures we get CPU time even when system is busy (which is when monitoring matters most)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, -10)  # Negative nice = higher priority
    except PermissionError:
        log.warning("qos_priority_failed", msg="Could not set high priority, running as normal")

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

    # Check for existing instance
    if self._check_already_running():
        log.error("daemon_already_running")
        raise RuntimeError("Daemon is already running")

    self._write_pid_file()

    # Initialize database
    await self._init_database()

    # Start caffeinate to prevent App Nap
    await self._start_caffeinate()

    self.state.running = True
    log.info("daemon_started")

    # Start auto-prune task
    self._auto_prune_task = asyncio.create_task(self._auto_prune())

    # Run main loop (powermetrics -> stress -> ring buffer -> tiers)
    # This replaces the old sentinel.start() call
    await self._main_loop()
```

**Step 2: Run daemon tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All PASS (may need to update some tests that mock sentinel)

**Step 3: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "feat(daemon): use main loop instead of sentinel"
```

---

## Phase 4: Add Socket Server

> **⚠️ SIMPLIFIED: Push-Based Design**
>
> Per the "Design Simplifications" section above, the socket server uses **push-based** streaming instead of poll-based. The main loop pushes directly to connected clients—no separate broadcast loop needed.
>
> Key changes from original task specs:
> - Remove `_broadcast_loop()` method
> - Remove `broadcast_interval_ms` parameter
> - Add `broadcast(stress, tier)` method called from main loop
> - `_handle_client` just manages connection lifecycle, doesn't send data

### Task 4.1: Create SocketServer Class (SIMPLIFIED)

**Files:**
- Create: `src/pause_monitor/socket_server.py`
- Create: `tests/test_socket_server.py`

**Step 1: Write the failing test**

```python
# tests/test_socket_server.py

import asyncio
import json
import pytest
from pathlib import Path

from pause_monitor.socket_server import SocketServer
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.stress import StressBreakdown


@pytest.mark.asyncio
async def test_socket_server_starts_and_stops(tmp_path):
    """SocketServer should start listening and stop cleanly."""
    socket_path = tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)

    await server.start()
    assert socket_path.exists()

    await server.stop()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_socket_server_streams_to_client(tmp_path):
    """SocketServer should stream ring buffer data to clients."""
    socket_path = tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    # Add samples (Phase 1: push requires metrics)
    metrics = PowermetricsResult(
        elapsed_ns=100_000_000,
        throttled=False,
        cpu_power=5.0,
        gpu_pct=10.0,
        gpu_power=1.0,
        io_read_per_s=1000.0,
        io_write_per_s=500.0,
        wakeups_per_s=100.0,
        pageins_per_s=0.0,
        top_cpu_processes=[],
        top_pagein_processes=[],
    )
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=15, wakeups=3, pageins=0)
    buffer.push(metrics, stress, tier=1)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read first message
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        message = json.loads(data.decode())

        assert "samples" in message
        assert "tier" in message
        assert len(message["samples"]) == 1
        assert message["samples"][0]["stress"]["load"] == 10
        assert message["samples"][0]["stress"]["gpu"] == 15

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/socket_server.py

"""Unix socket server for streaming ring buffer data to TUI."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pause_monitor.ringbuffer import RingBuffer

log = logging.getLogger(__name__)


class StressDict(TypedDict):
    """Stress breakdown as dict for JSON serialization."""
    load: int
    memory: int
    thermal: int
    latency: int
    io: int
    gpu: int
    wakeups: int


class SampleDict(TypedDict):
    """Ring buffer sample as dict for JSON serialization."""
    timestamp: float
    stress: StressDict
    tier: int


class SocketMessage(TypedDict):
    """Message sent from daemon to TUI via socket."""
    samples: list[SampleDict]
    tier: int
    current_stress: StressDict | None
    sample_count: int


class SocketServer:
    """Unix domain socket server for real-time streaming to TUI.

    PUSH-BASED DESIGN (per Design Simplifications):
    - Main loop calls broadcast() after each powermetrics sample
    - No internal polling loop - data flows directly from daemon
    - Protocol: newline-delimited JSON messages
    """

    def __init__(
        self,
        socket_path: Path,
        ring_buffer: RingBuffer,
    ):
        self.socket_path = socket_path
        self.ring_buffer = ring_buffer
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._running = False
        # REMOVED: broadcast_interval_ms, _broadcast_task (push-based, not poll-based)

    @property
    def has_clients(self) -> bool:
        """Check if any clients are connected (for main loop optimization)."""
        return len(self._clients) > 0

    async def start(self) -> None:
        """Start the socket server."""
        # Remove stale socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self._running = True
        # REMOVED: self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        log.info("socket_server_started", path=str(self.socket_path))

    async def stop(self) -> None:
        """Stop the socket server."""
        self._running = False

        # REMOVED: _broadcast_task cancellation (no longer exists)

        # Close all client connections
        for writer in list(self._clients):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        log.info("socket_server_stopped")

    async def broadcast(
        self,
        metrics: PowermetricsResult,
        stress: StressBreakdown,
        tier: int,
    ) -> None:
        """Push current sample to all connected clients.
        
        Called from main loop after each powermetrics sample.
        This is the push-based approach - no internal polling.
        
        Args:
            metrics: Raw powermetrics data (Phase 1 format)
            stress: Computed stress breakdown
            tier: Current tier (1, 2, or 3)
        """
        if not self._clients:
            return
        
        # Build message with current sample data
        message: SocketMessage = {
            "timestamp": datetime.now().isoformat(),
            "tier": tier,
            "stress": StressDict(**asdict(stress)),
            "metrics": {
                "io_read_per_s": metrics.io_read_per_s,
                "io_write_per_s": metrics.io_write_per_s,
                "wakeups_per_s": metrics.wakeups_per_s,
                "gpu_pct": metrics.gpu_pct,
                "cpu_power": metrics.cpu_power,
                "gpu_power": metrics.gpu_power,
                "throttled": metrics.throttled,
            },
            "sample_count": len(self.ring_buffer.samples),
        }
        
        data = json.dumps(message).encode() + b"\n"
        
        # Send to all clients, removing any that fail
        for writer in list(self._clients):
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                self._clients.discard(writer)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection."""
        self._clients.add(writer)
        log.info("socket_client_connected", count=len(self._clients))

        try:
            # Send initial state from ring buffer
            await self._send_initial_state(writer)

            # Keep connection alive until client disconnects
            # Client just needs to stay connected; data comes via broadcast()
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=1.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    continue
                except ConnectionError:
                    break
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("socket_client_disconnected", count=len(self._clients))

    # REMOVED: async def _broadcast_loop(self) - push-based, not poll-based

    async def _send_initial_state(self, writer: asyncio.StreamWriter) -> None:
        """Send current ring buffer state to a newly connected client."""
        samples = self.ring_buffer.samples
        latest = samples[-1] if samples else None

        message: SocketMessage = {
            "type": "initial_state",
            "samples": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "stress": StressDict(**asdict(s.stress)),
                    "tier": s.tier,
                    # Include raw metrics from Phase 1 RingSample
                    "metrics": {
                        "io_read_per_s": s.metrics.io_read_per_s,
                        "io_write_per_s": s.metrics.io_write_per_s,
                        "wakeups_per_s": s.metrics.wakeups_per_s,
                        "gpu_pct": s.metrics.gpu_pct,
                        "throttled": s.metrics.throttled,
                    },
                }
                for s in samples[-30:]  # Last 3 seconds
            ],
            "tier": latest.tier if latest else 1,
            "current_stress": StressDict(**asdict(latest.stress)) if latest else None,
            "sample_count": len(samples),
        }

        data = json.dumps(message).encode() + b"\n"
        writer.write(data)
        await writer.drain()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/socket_server.py tests/test_socket_server.py
git commit -m "feat: add Unix socket server for TUI streaming"
```

---

### Task 4.2: Integrate SocketServer into Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `src/pause_monitor/config.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

@pytest.mark.asyncio
async def test_daemon_socket_available_after_start(tmp_path, monkeypatch):
    """Daemon should have socket server listening after start."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)

    # Mock _main_loop to exit immediately (we just want to test socket wiring)
    async def mock_main_loop():
        pass
    monkeypatch.setattr(daemon, "_main_loop", mock_main_loop)

    # Start daemon (will return after mock_main_loop completes)
    await daemon.start()

    # Socket file should exist and server should be listening
    assert config.socket_path.exists(), "Socket file should exist after daemon start"

    # Verify we can connect
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    writer.close()
    await writer.wait_closed()

    await daemon.stop()
    assert not config.socket_path.exists(), "Socket file should be cleaned up after stop"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_socket_available_after_start -v`
Expected: FAIL (no socket server integration yet)

**Step 3: Add socket_path to Config**

```python
# src/pause_monitor/config.py - add to Config class

@property
def socket_path(self) -> Path:
    """Path to daemon Unix socket."""
    return self.data_dir / "daemon.sock"
```

**Step 4: Add SocketServer to Daemon**

Add import at top of `src/pause_monitor/daemon.py`:
```python
from pause_monitor.socket_server import SocketServer
```

Add to `Daemon.__init__`:
```python
self._socket_server: SocketServer | None = None
```

Add to `Daemon.start()` (after caffeinate, before `self.state.running = True`):
```python
# Start socket server for TUI (push-based - no broadcast_interval_ms)
self._socket_server = SocketServer(
    socket_path=self.config.socket_path,
    ring_buffer=self.ring_buffer,
)
await self._socket_server.start()
```

Update `Daemon.stop()`:
```python
if self._socket_server:
    await self._socket_server.stop()
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_socket_available_after_start -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/daemon.py src/pause_monitor/config.py
git commit -m "feat(daemon): integrate socket server for TUI"
```

---

## Phase 5: Update TUI to Use Socket

> **⚠️ SIMPLIFIED: No Auto-Reconnect**
>
> Per the "Design Simplifications" section above, the socket client is **simple and stateless**. It connects or throws. The TUI decides what to do on disconnect.
>
> Key changes from original task specs:
> - Remove `on_disconnect` / `on_reconnect` callbacks
> - Remove `reconnect_interval` parameter
> - Remove `_reconnect()` and `_read_loop()` methods
> - `connect()` raises `FileNotFoundError` if daemon not running
> - `read_message()` raises `ConnectionError` on disconnect
> - TUI handles reconnection logic in its own event loop

### Task 5.1: Create SocketClient Class (SIMPLIFIED)

**Files:**
- Create: `src/pause_monitor/socket_client.py`
- Create: `tests/test_socket_client.py`

**Step 1: Write the failing test**

```python
# tests/test_socket_client.py

import asyncio
import json
import pytest
from pathlib import Path

from pause_monitor.socket_client import SocketClient


@pytest.mark.asyncio
async def test_socket_client_receives_data(tmp_path):
    """SocketClient should receive and parse messages."""
    socket_path = tmp_path / "test.sock"

    # Start mock server
    async def handle_client(reader, writer):
        msg = {"samples": [], "tier": 2, "current_stress": {"load": 5}}
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

    try:
        client = SocketClient(socket_path=socket_path)
        await client.connect()

        # Read one message
        data = await client.read_message()
        assert data["tier"] == 2

        await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_socket_client_raises_on_connection_failure(tmp_path):
    """SocketClient should raise FileNotFoundError if daemon not running."""
    socket_path = tmp_path / "nonexistent.sock"

    client = SocketClient(socket_path=socket_path)

    with pytest.raises(FileNotFoundError):
        await client.connect()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_socket_client.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/socket_client.py

"""Unix socket client for receiving ring buffer data from daemon."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class SocketClient:
    """Unix domain socket client for real-time ring buffer data.

    Simple and stateless: connects or throws. TUI handles reconnection.
    """

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.on_data: Callable[[dict[str, Any]], None] | None = None

    @property
    def connected(self) -> bool:
        """Whether client is connected."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect to the daemon socket.
        
        Raises:
            FileNotFoundError: If socket doesn't exist (daemon not running)
        """
        if not self.socket_path.exists():
            raise FileNotFoundError(f"Socket not found: {self.socket_path}")

        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self.socket_path)
        )
        log.info("socket_client_connected", path=str(self.socket_path))

    async def disconnect(self) -> None:
        """Disconnect from the daemon socket."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        log.info("socket_client_disconnected")

    async def read_message(self) -> dict[str, Any]:
        """Read next message from socket.
        
        Returns:
            Parsed JSON message from daemon
            
        Raises:
            ConnectionError: If connection is lost
            json.JSONDecodeError: If message is invalid JSON
        """
        if not self._reader:
            raise ConnectionError("Not connected")

        line = await self._reader.readline()
        if not line:
            raise ConnectionError("Connection closed by server")

        return json.loads(line.decode())
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_socket_client.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/socket_client.py tests/test_socket_client.py
git commit -m "feat: add Unix socket client for TUI"
```

---

### Task 5.2: Update TUI to Connect via Socket

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Create: `tests/test_tui_connection.py`

**Step 1: Write the failing test for fallback logic**

```python
# tests/test_tui_connection.py

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from pause_monitor.config import Config


@pytest.mark.asyncio
async def test_tui_uses_socket_when_available(tmp_path):
    """TUI should connect via socket when daemon is running."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    config._data_dir = tmp_path

    # Create a fake socket file to simulate daemon running
    config.socket_path.touch()

    app = PauseMonitorApp(config)

    with patch.object(app, '_socket_client') as mock_client:
        mock_client.connect = AsyncMock()
        # Simulate successful socket connection
        with patch('pause_monitor.tui.app.SocketClient') as MockSocketClient:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            MockSocketClient.return_value = mock_instance

            await app.on_mount()

            assert app._use_socket is True
            mock_instance.connect.assert_called_once()


@pytest.mark.asyncio
async def test_tui_shows_waiting_state_when_no_daemon(tmp_path):
    """TUI should show waiting state when daemon not running."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    config._data_dir = tmp_path

    # No socket file (daemon not running)
    assert not config.socket_path.exists()

    app = PauseMonitorApp(config)

    with patch.object(app, 'notify'):  # Don't actually notify
        with patch('asyncio.create_task'):  # Don't start background task
            await app.on_mount()

    assert "waiting" in app.sub_title.lower()


def test_tui_updates_subtitle_on_disconnect():
    """TUI should show error state when daemon connection is lost."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)
    app.sub_title = "System Health Monitor (live)"

    # Simulate connection error
    app._set_disconnected()

    assert "not running" in app.sub_title.lower() or "error" in app.sub_title.lower()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_connection.py -v`
Expected: FAIL (TUI doesn't have socket logic yet)

**Step 3: Add socket client import and attribute**

Add imports at top of `src/pause_monitor/tui/app.py`:
```python
from typing import Any
from pause_monitor.socket_client import SocketClient
```

Add to `PauseMonitorApp.__init__`:
```python
self._socket_client: SocketClient | None = None
```

**Step 4: Update on_mount to connect to daemon**

Replace or update the `on_mount` method:
```python
async def on_mount(self) -> None:
    """Initialize on startup."""
    self.title = "pause-monitor"
    
    # Create socket client
    self._socket_client = SocketClient(socket_path=self.config.socket_path)

    # Try initial connection
    try:
        await self._socket_client.connect()
        self.sub_title = "System Health Monitor (live)"
        log.info("tui_connected_via_socket")
        # Start reading messages
        asyncio.create_task(self._read_socket_loop())
    except FileNotFoundError:
        # Daemon not running - show error state
        self._set_disconnected()
        self.notify(
            "Daemon not running. Start with: sudo pause-monitor daemon",
            severity="warning",
        )

async def _read_socket_loop(self) -> None:
    """Read messages from socket and update UI."""
    try:
        while True:
            data = await self._socket_client.read_message()
            self._handle_socket_data(data)
    except ConnectionError:
        self._set_disconnected()
        log.warning("tui_daemon_disconnected")

def _set_disconnected(self) -> None:
    """Update UI to show disconnected state."""
    self.sub_title = "System Health Monitor (daemon not running)"
```

**Step 5: Add socket data handler**

```python
def _handle_socket_data(self, data: dict[str, Any]) -> None:
    """Handle real-time data from daemon socket."""
    current_stress = data.get("current_stress")
    tier = data.get("tier", 1)

    if not current_stress:
        return

    # Calculate total stress
    total = sum(current_stress.values())

    # Update stress gauge
    try:
        stress_gauge = self.query_one("#stress-gauge", StressGauge)
        stress_gauge.update_stress(total)
    except Exception:
        pass

    # Update stress breakdown
    try:
        breakdown = self.query_one("#breakdown", Static)
        breakdown.update(
            f"Load: {current_stress.get('load', 0):3d}  "
            f"Memory: {current_stress.get('memory', 0):3d}  "
            f"GPU: {current_stress.get('gpu', 0):3d}\n"
            f"Thermal: {current_stress.get('thermal', 0):3d}  "
            f"Latency: {current_stress.get('latency', 0):3d}  "
            f"Wakeups: {current_stress.get('wakeups', 0):3d}\n"
            f"I/O: {current_stress.get('io', 0):3d}  "
            f"Tier: {tier}"
        )
    except Exception:
        pass
```

**Step 6: Update on_unmount**

```python
async def on_unmount(self) -> None:
    """Clean up on shutdown."""
    if self._socket_client:
        await self._socket_client.disconnect()
```

**Step 7: Run tests to verify connection logic**

Run: `uv run pytest tests/test_tui_connection.py -v`
Expected: PASS

**Step 8: Manual testing**

```bash
# Terminal 1: Start daemon (needs sudo for powermetrics)
sudo uv run pause-monitor daemon

# Terminal 2: Start TUI
uv run pause-monitor tui
# Should show "(live)" in subtitle
```

**Step 9: Commit**

```bash
git add src/pause_monitor/tui/app.py tests/test_tui_connection.py
git commit -m "feat(tui): connect via socket for real-time data"
```

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
