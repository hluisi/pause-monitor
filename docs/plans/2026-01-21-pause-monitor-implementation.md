# pause-monitor Redesign Implementation Plan

for design 2026-01-21-pause-monitor-redesign.md

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable real-time 10Hz TUI dashboard with complete 7-factor stress monitoring, tier-appropriate forensics, and Unix socket streaming.

**Architecture:** Single 100ms loop driven by powermetrics. Ring buffer is the source of truth. Socket streams to TUI. SQLite stores only tier events (elevated bookmarks, pause forensics).

**Tech Stack:** Python 3.14, asyncio, Unix domain sockets, Textual TUI, SQLite (history only)

---

## Pre-Implementation Cleanup (Completed 2026-01-22)

Before beginning implementation, the following dead code was removed from the codebase. This cleanup was necessary because the codebase had evolved from a SamplePolicy-based architecture to a Sentinel-based architecture, but the old code was left in place "for backwards compatibility." Since we have no external users and no need for backwards compatibility, this dead code was removed.

### Why This Cleanup Was Necessary

The audit revealed:
- **Two competing architectures**: `SamplePolicy` (never called) vs `Sentinel` (active)
- **Dead methods**: `_run_loop`, `_collect_sample`, `_check_for_pause`, `_handle_pause`, `_handle_policy_result` — all defined but never invoked in the production code path
- **Orphaned components**: `self.policy`, `self.pause_detector`, `self._powermetrics` — initialized but never used
- **Tests testing dead code**: 11 tests that exercised the dead code paths

### What Was Removed

**From `collector.py`:**
- `SamplePolicy` class
- `SamplingState` enum
- `PolicyResult` dataclass

**From `daemon.py`:**
- `_run_loop()` method (replaced by `sentinel.start()`)
- `_collect_sample()` method (dead code)
- `_check_for_pause()` method (replaced by sentinel's pause detection)
- `_handle_pause()` method (replaced by `_handle_pause_from_sentinel()`)
- `_handle_policy_result()` method (dead code)
- `self.policy` initialization
- `self.pause_detector` initialization  
- `self._powermetrics` field and cleanup code
- Unused imports: `PolicyResult`, `PowermetricsResult`, `PowermetricsStream`, `SamplingState`, `get_system_metrics`, `PauseDetector`, `PauseEvent`, `Sample`, `insert_sample`, `calculate_stress`, `get_memory_pressure_fast`, `time`

**From tests:**
- 5 `test_sample_policy_*` tests (testing deleted class)
- `test_daemon_stop_cleans_up` (testing deleted `_powermetrics`)
- `test_daemon_collects_sample` (testing deleted `_collect_sample`)
- `test_daemon_detects_pause` (testing deleted `_check_for_pause`)
- 3 GPU tests using `_collect_sample` (testing deleted method)
- Updated `test_daemon_init_creates_components` to remove assertions about deleted fields

**Result:** 264 tests pass, linter clean. Tasks 5.2 and 5.3 are now complete.

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
│   ├── cpu_power: real (watts)
│   └── combined_power: real (total SoC power in watts)
└── gpu: dict
    ├── freq_hz: real
    ├── idle_ratio: real (1.0 = fully idle, 0.0 = fully busy)
    └── gpu_power: real (watts)
```

### Field Mapping: powermetrics → PowermetricsResult

| powermetrics key | PowermetricsResult field | Type | Transform | Why |
|------------------|--------------------------|------|-----------|-----|
| `elapsed_ns` | `elapsed_ns` | int | direct | Actual interval for latency ratio calculation |
| `thermal_pressure` | `throttled` | bool | `!= "Nominal"` | Simplify to throttled/not-throttled for stress scoring |
| `processor.cpu_power` | `cpu_power` | float | direct | Power indicates load better than frequency |
| `processor.combined_power` | `combined_power` | float | direct | Total SoC power for trend analysis |
| `gpu.idle_ratio` | `gpu_pct` | float | `(1 - idle_ratio) * 100` | Convert to familiar percentage |
| `gpu.gpu_power` | `gpu_power` | float | direct | Power indicates GPU work better than frequency |
| `disk.rbytes_per_s` | `io_read_per_s` | float | direct | Keep read/write separate for culprit ID |
| `disk.wbytes_per_s` | `io_write_per_s` | float | direct | Write-heavy vs read-heavy workloads differ |
| `tasks` | `top_processes` | list | Top 10 by cputime_ms_per_s | For culprit identification |
| Sum of `tasks[].idle_wakeups_per_s` | `wakeups_per_s` | float | sum all tasks | System-wide wakeup rate |

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
| cpu_power | cpu_power | REAL | Power trend analysis |
| gpu_pct | gpu_pct | REAL | For historical charts |
| io_read_per_s | io_read_per_s | REAL | For I/O trend analysis |
| io_write_per_s | io_write_per_s | REAL | For I/O trend analysis |
| throttled | throttled | INTEGER | 0/1 for thermal tracking |
| wakeups_per_s | wakeups_per_s | REAL | For wakeup trend analysis |

**Note:** `cpu_pct`, `load_avg`, `mem_available`, `swap_used`, `net_sent`, `net_recv` are NOT from powermetrics. These come from:
- `load_avg`: `os.getloadavg()[0]`
- `mem_available`: `sysctl hw.memsize` minus wired/active (or `vm_stat`)
- Memory pressure: `sysctl kern.memorystatus_level` (0-100 scale, inverted)

### Design Decisions with Rationale

| Decision | Rationale |
|----------|-----------|
| **Use `idle_wakeups_per_s` not `intr_wakeups_per_s`** | Idle wakeups indicate a process waking from idle state (energy impact). Interrupt wakeups can be normal system activity. |
| **Store rates, not cumulative values** | powermetrics already computes rates. Storing rates means samples are directly comparable regardless of interval length. |
| **Keep `io_read` and `io_write` separate** | Distinguishes read-heavy (database queries) from write-heavy (logging, backups) workloads. Combined I/O obscures the cause. |
| **Sum wakeups across all processes** | Individual process wakeups matter for culprit ID, but system-wide total indicates scheduler pressure. |
| **Use `thermal_pressure` string, not temp** | Apple silicon doesn't expose CPU temperature via powermetrics. Thermal pressure is the actionable signal. |
| **`gpu_pct` from `1 - idle_ratio`** | GPU "busy" is complement of idle. 96% idle = 4% busy. More intuitive as percentage. |
| **Top 10 processes by CPU time** | Captures the culprits without storing hundreds of processes per sample. |
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
cpu_power       REAL,   -- watts from processor.cpu_power
gpu_power       REAL,   -- watts from gpu.gpu_power
```

### Code Cleanup Required (Part of Phase 1)

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

## Architecture Overview

```
powermetrics (100ms stream)
        │
        ▼
┌─────────────────────────────────────┐
│  Daemon Main Loop                   │
│  - Parse powermetrics sample        │
│  - Calculate stress (7 factors)     │
│  - Measure latency (pause detect)   │
│  - Push to ring buffer              │
│  - Update TierManager               │
└─────────────────────────────────────┘
        │
        ├──▶ Ring Buffer (30s, 300 samples)
        │           │
        │           └──▶ Socket Server ──▶ TUI (10Hz real-time)
        │
        └──▶ TierManager
                │
                ├── Tier 1: Nothing
                ├── Tier 2 exit: Write bookmark to SQLite
                └── Tier 3/Pause: Full forensics + SQLite
```

**Tier Forensics:**

| Tier | Event | Data Captured |
|------|-------|---------------|
| 1 (SENTINEL) | None | Ring buffer only (ephemeral) |
| 2 (ELEVATED) | Bookmark on exit | start_time, end_time, duration, peak_stress, top_process_at_peak |
| 3 (CRITICAL) | Full on pause | Ring buffer freeze + spindump + tailspin + logs + process snapshot |

---

## Phase 1: Update PowermetricsStream for 100ms + Complete Data

> **⚠️ IMPORTANT: Tasks 1.3+ Have Incorrect Assumptions**
>
> The original task specifications were written before examining actual powermetrics output.
> The **Data Dictionary** section above (based on real `/tmp/powermetrics-sample.plist` capture)
> is authoritative. Key corrections:
>
> | Original Plan | Correction (per Data Dictionary) |
> |---------------|----------------------------------|
> | `io_bytes_per_sec` (combined) | `io_read_per_s` and `io_write_per_s` (separate — needed for culprit ID) |
> | `wakeups_per_sec` from nested `wakeups[]` | `wakeups_per_s` = sum of `tasks[].idle_wakeups_per_s` (direct field, not nested) |
> | `gpu_pct` from `gpu.busy_percent` | `gpu_pct` = `(1 - gpu.idle_ratio) * 100` (busy_percent doesn't exist) |
> | Missing fields | Add `elapsed_ns`, `cpu_power`, `gpu_power` |
>
> When implementing, **follow the Data Dictionary**, not the original task code snippets below.

### Corrected PowermetricsResult (Use This, Not Task 1.3 Snippets)

```python
@dataclass
class PowermetricsResult:
    """Parsed powermetrics sample data.
    
    All fields derived from powermetrics plist output. See Data Dictionary
    for field mappings and rationale.
    """
    # Timing
    elapsed_ns: int  # Actual sample interval (for latency ratio)
    
    # CPU (from processor dict)
    cpu_pct: float | None  # Computed from cluster idle_ratio
    cpu_power: float | None  # Watts from processor.cpu_power
    
    # Thermal
    throttled: bool  # True if thermal_pressure != "Nominal"
    
    # GPU (from gpu dict)
    gpu_pct: float | None  # (1 - idle_ratio) * 100
    gpu_power: float | None  # Watts from gpu.gpu_power
    
    # Disk I/O (from disk dict) — kept separate for culprit identification
    io_read_per_s: float  # bytes/sec from disk.rbytes_per_s
    io_write_per_s: float  # bytes/sec from disk.wbytes_per_s
    
    # Wakeups (summed from tasks array)
    wakeups_per_s: float  # Sum of tasks[].idle_wakeups_per_s
    
    # Top processes for culprit identification
    top_processes: list[dict]  # [{name, pid, cpu_ms_per_s, io_bytes_per_s, wakeups_per_s}]
```

```python
def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.
    
    Extracts metrics per the Data Dictionary field mappings.
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException:
        log.warning("invalid_plist_data")
        return PowermetricsResult(
            elapsed_ns=0,
            cpu_pct=None, cpu_power=None,
            throttled=False,
            gpu_pct=None, gpu_power=None,
            io_read_per_s=0.0, io_write_per_s=0.0,
            wakeups_per_s=0.0,
            top_processes=[],
        )
    
    # Timing
    elapsed_ns = plist.get("elapsed_ns", 0)
    
    # Thermal throttling: anything other than "Nominal" means throttled
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure != "Nominal"
    
    # CPU from processor dict
    processor = plist.get("processor", {})
    cpu_power = processor.get("cpu_power")  # Watts
    cpu_pct = _extract_cpu_usage(processor)  # Existing helper
    
    # GPU from gpu dict: busy = 1 - idle_ratio
    gpu_data = plist.get("gpu", {})
    idle_ratio = gpu_data.get("idle_ratio")
    gpu_pct = (1.0 - idle_ratio) * 100.0 if idle_ratio is not None else None
    gpu_power = gpu_data.get("gpu_power")  # Watts
    
    # Disk I/O — keep read/write separate
    disk_data = plist.get("disk", {})
    io_read_per_s = disk_data.get("rbytes_per_s", 0.0)
    io_write_per_s = disk_data.get("wbytes_per_s", 0.0)
    
    # Tasks: sum wakeups, collect top processes
    wakeups_per_s = 0.0
    top_processes: list[dict] = []
    
    for task in plist.get("tasks", []):
        # Idle wakeups are the energy-relevant ones
        task_wakeups = task.get("idle_wakeups_per_s", 0.0)
        wakeups_per_s += task_wakeups
        
        # Collect process info for culprit identification
        proc = {
            "name": task.get("name", "unknown"),
            "pid": task.get("pid", 0),
            "cpu_ms_per_s": task.get("cputime_ms_per_s", 0.0),
            "io_bytes_per_s": task.get("diskio_bytesread_per_s", 0.0) 
                           + task.get("diskio_byteswritten_per_s", 0.0),
            "wakeups_per_s": task_wakeups,
        }
        top_processes.append(proc)
    
    # Sort by CPU usage descending, keep top 10
    top_processes.sort(key=lambda p: p["cpu_ms_per_s"], reverse=True)
    top_processes = top_processes[:10]
    
    return PowermetricsResult(
        elapsed_ns=elapsed_ns,
        cpu_pct=cpu_pct,
        cpu_power=cpu_power,
        throttled=throttled,
        gpu_pct=gpu_pct,
        gpu_power=gpu_power,
        io_read_per_s=io_read_per_s,
        io_write_per_s=io_write_per_s,
        wakeups_per_s=wakeups_per_s,
        top_processes=top_processes,
    )
```

### Task 1.1: Change PowermetricsStream Interval to 100ms

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py - add to existing file

def test_powermetrics_stream_default_interval_is_100ms():
    """PowermetricsStream should default to 100ms for 10Hz sampling."""
    stream = PowermetricsStream()
    assert stream.interval_ms == 100
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_default_interval_is_100ms -v`
Expected: FAIL (currently defaults to 1000)

**Step 3: Write minimal implementation**

Change `PowermetricsStream.__init__`:
```python
def __init__(self, interval_ms: int = 100):  # Changed from 1000
    self.interval_ms = interval_ms
    ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_default_interval_is_100ms -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): change powermetrics default to 100ms (10Hz)"
```

---

### Task 1.2: Add Tasks and Disk Samplers

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py - add to existing file

def test_powermetrics_stream_includes_tasks_and_disk_samplers():
    """PowermetricsStream should include tasks and disk samplers."""
    stream = PowermetricsStream()
    assert "tasks" in stream.POWERMETRICS_CMD
    assert "disk" in stream.POWERMETRICS_CMD
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_includes_tasks_and_disk_samplers -v`
Expected: FAIL (current samplers are cpu_power,gpu_power,thermal)

**Step 3: Write minimal implementation**

Update `PowermetricsStream.POWERMETRICS_CMD`:
```python
POWERMETRICS_CMD = [
    "/usr/bin/powermetrics",
    "--samplers",
    "cpu_power,gpu_power,thermal,tasks,disk",  # tasks for wakeups, disk for I/O
    "-f",
    "plist",
]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_includes_tasks_and_disk_samplers -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add tasks and disk samplers for wakeups and I/O"
```

---

### Task 1.3: Add Wakeups and Top Processes to PowermetricsResult

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write the failing tests**

```python
# tests/test_collector.py - add to existing file

def test_powermetrics_result_has_wakeups():
    """PowermetricsResult should include system-wide wakeups."""
    # Sample plist with tasks data
    plist_data = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>tasks</key>
    <array>
        <dict>
            <key>name</key>
            <string>test_process</string>
            <key>pid</key>
            <integer>123</integer>
            <key>wakeups</key>
            <array>
                <dict>
                    <key>wakeups_per_s</key>
                    <real>50.0</real>
                </dict>
            </array>
            <key>cputime_ms_per_s</key>
            <real>100.0</real>
        </dict>
        <dict>
            <key>name</key>
            <string>another_process</string>
            <key>pid</key>
            <integer>456</integer>
            <key>wakeups</key>
            <array>
                <dict>
                    <key>wakeups_per_s</key>
                    <real>30.0</real>
                </dict>
            </array>
            <key>cputime_ms_per_s</key>
            <real>50.0</real>
        </dict>
    </array>
</dict>
</plist>'''

    result = parse_powermetrics_sample(plist_data)
    assert result.wakeups_per_sec == 80.0  # Sum of all process wakeups


def test_powermetrics_result_has_top_processes():
    """PowermetricsResult should include top processes by CPU."""
    plist_data = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>tasks</key>
    <array>
        <dict>
            <key>name</key>
            <string>high_cpu</string>
            <key>pid</key>
            <integer>100</integer>
            <key>cputime_ms_per_s</key>
            <real>500.0</real>
        </dict>
        <dict>
            <key>name</key>
            <string>low_cpu</string>
            <key>pid</key>
            <integer>200</integer>
            <key>cputime_ms_per_s</key>
            <real>50.0</real>
        </dict>
    </array>
</dict>
</plist>'''

    result = parse_powermetrics_sample(plist_data)
    assert len(result.top_processes) == 2
    assert result.top_processes[0]["name"] == "high_cpu"
    assert result.top_processes[0]["cpu_ms_per_s"] == 500.0


def test_powermetrics_result_missing_tasks_returns_defaults():
    """PowermetricsResult should handle missing tasks gracefully."""
    plist_data = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>processor</key>
    <dict></dict>
</dict>
</plist>'''

    result = parse_powermetrics_sample(plist_data)
    assert result.wakeups_per_sec == 0.0
    assert result.top_processes == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_result_has_wakeups tests/test_collector.py::test_powermetrics_result_has_top_processes tests/test_collector.py::test_powermetrics_result_missing_tasks_returns_defaults -v`
Expected: FAIL (PowermetricsResult has no wakeups_per_sec or top_processes)

**Step 3: Write implementation**

Update `PowermetricsResult` dataclass in `src/pause_monitor/collector.py`:
```python
@dataclass
class PowermetricsResult:
    """Parsed powermetrics sample data."""

    cpu_pct: float | None
    cpu_freq: int | None  # MHz
    cpu_temp: float | None
    throttled: bool | None
    gpu_pct: float | None
    wakeups_per_sec: float  # System-wide idle wakeups/sec (default 0.0)
    io_bytes_per_sec: float  # Combined read+write bytes/sec (default 0.0)
    top_processes: list[dict]  # Top processes by CPU [{name, pid, cpu_ms_per_s}]
```

Replace the entire `parse_powermetrics_sample` function:
```python
def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.

    Args:
        data: Raw plist bytes from powermetrics output

    Returns:
        PowermetricsResult with extracted metrics
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException:
        log.warning("invalid_plist_data")
        return PowermetricsResult(
            cpu_pct=None,
            cpu_freq=None,
            cpu_temp=None,
            throttled=None,
            gpu_pct=None,
            wakeups_per_sec=0.0,
            io_bytes_per_sec=0.0,
            top_processes=[],
        )

    # Extract CPU usage from cluster data
    cpu_pct = _extract_cpu_usage(plist.get("processor", {}))

    # Extract max CPU frequency
    cpu_freq = _extract_cpu_freq(plist.get("processor", {}))

    # CPU temperature (not always available)
    cpu_temp = None
    if "processor" in plist and "cpu_thermal_level" in plist["processor"]:
        cpu_temp = plist["processor"]["cpu_thermal_level"]

    # Thermal throttling
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure in ("Moderate", "Heavy", "Critical", "Sleeping")

    # GPU usage
    gpu_data = plist.get("gpu", {})
    gpu_pct = gpu_data.get("busy_percent")

    # Extract disk I/O (combined read + write bytes/sec)
    disk_data = plist.get("disk", {})
    io_bytes_per_sec = disk_data.get("rbytes_per_s", 0.0) + disk_data.get("wbytes_per_s", 0.0)

    # Extract wakeups and top processes from tasks
    wakeups_per_sec = 0.0
    top_processes: list[dict] = []
    tasks = plist.get("tasks", [])
    for task in tasks:
        # Sum wakeups
        wakeups_list = task.get("wakeups", [])
        for w in wakeups_list:
            wakeups_per_sec += w.get("wakeups_per_s", 0.0)

        # Collect process info
        proc = {
            "name": task.get("name", "unknown"),
            "pid": task.get("pid", 0),
            "cpu_ms_per_s": task.get("cputime_ms_per_s", 0.0),
        }
        top_processes.append(proc)

    # Sort by CPU usage descending, keep top 10
    top_processes.sort(key=lambda p: p["cpu_ms_per_s"], reverse=True)
    top_processes = top_processes[:10]

    return PowermetricsResult(
        cpu_pct=cpu_pct,
        cpu_freq=cpu_freq,
        cpu_temp=cpu_temp,
        throttled=throttled,
        gpu_pct=gpu_pct,
        wakeups_per_sec=wakeups_per_sec,
        io_bytes_per_sec=io_bytes_per_sec,
        top_processes=top_processes,
    )
```

**Step 4: Update ALL code that constructs PowermetricsResult**

There are exactly 6 locations that construct `PowermetricsResult`. Update each one:

**4a. `src/pause_monitor/collector.py` — error return in `parse_powermetrics_sample` (the `except plistlib.InvalidFileException` block):**
```python
        return PowermetricsResult(
            cpu_pct=None,
            cpu_freq=None,
            cpu_temp=None,
            throttled=None,
            gpu_pct=None,
            wakeups_per_sec=0.0,
            io_bytes_per_sec=0.0,
            top_processes=[],
        )
```

**4b. `src/pause_monitor/collector.py` — normal return at end of `parse_powermetrics_sample` - already updated in Step 3 above**

**4c. `tests/test_daemon.py` — in `test_daemon_collects_sample`:**
```python
                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=10.0,
                    wakeups_per_sec=0.0,
                    io_bytes_per_sec=0.0,
                    top_processes=[],
                )
```

**4d. `tests/test_daemon.py` — in `test_daemon_passes_gpu_to_stress_calculation`:**
```python
                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=85.0,
                    wakeups_per_sec=0.0,
                    io_bytes_per_sec=0.0,
                    top_processes=[],
                )
```

**4e. `tests/test_daemon.py` — in `test_daemon_handles_none_gpu`:**
```python
                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=None,
                    wakeups_per_sec=0.0,
                    io_bytes_per_sec=0.0,
                    top_processes=[],
                )
```

**4f. `tests/test_daemon.py` — in `test_daemon_gpu_below_threshold_no_stress`:**
```python
                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=50.0,
                    wakeups_per_sec=0.0,
                    io_bytes_per_sec=0.0,
                    top_processes=[],
                )
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_collector.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add wakeups_per_sec and top_processes to PowermetricsResult"
```

---

### Task 1.4: Add Powermetrics Startup Validation

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
# tests/test_collector.py - add to existing file

import pytest


@pytest.mark.asyncio
async def test_powermetrics_stream_raises_on_permission_denied(monkeypatch):
    """PowermetricsStream.start() should raise if powermetrics fails to start."""
    import asyncio

    async def mock_create_subprocess(*args, **kwargs):
        # Simulate permission denied
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess)

    stream = PowermetricsStream()
    with pytest.raises(RuntimeError, match="powermetrics failed to start"):
        await stream.start()


@pytest.mark.asyncio
async def test_powermetrics_stream_raises_on_not_found(monkeypatch):
    """PowermetricsStream.start() should raise if powermetrics not found."""
    import asyncio

    async def mock_create_subprocess(*args, **kwargs):
        raise FileNotFoundError("powermetrics not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess)

    stream = PowermetricsStream()
    with pytest.raises(RuntimeError, match="powermetrics not found"):
        await stream.start()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_raises_on_permission_denied tests/test_collector.py::test_powermetrics_stream_raises_on_not_found -v`
Expected: FAIL (currently doesn't wrap exceptions)

**Step 3: Write minimal implementation**

Update `PowermetricsStream.start()` in `src/pause_monitor/collector.py`:
```python
async def start(self) -> None:
    """Start the powermetrics subprocess.

    Raises:
        RuntimeError: If powermetrics fails to start (permission denied, not found, etc.)
    """
    cmd = self.POWERMETRICS_CMD + ["-i", str(self.interval_ms)]

    try:
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except PermissionError as e:
        raise RuntimeError(
            f"powermetrics failed to start: {e}. "
            "Daemon requires root privileges (sudo) to run powermetrics."
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            f"powermetrics not found: {e}. "
            "Ensure /usr/bin/powermetrics exists (macOS only)."
        ) from e
    except OSError as e:
        raise RuntimeError(f"powermetrics failed to start: {e}") from e

    self._running = True
    log.info("powermetrics_started", interval_ms=self.interval_ms)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_raises_on_permission_denied tests/test_collector.py::test_powermetrics_stream_raises_on_not_found -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): fail fast if powermetrics unavailable"
```

---

### Task 1.5: Add Pause Detection Threshold to Config

**Files:**
- Modify: `src/pause_monitor/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# tests/test_config.py - add to existing file

def test_sentinel_config_has_pause_threshold():
    """SentinelConfig should have pause detection threshold."""
    from pause_monitor.config import SentinelConfig

    config = SentinelConfig()
    assert config.pause_threshold_ratio == 2.0  # Default: 2x expected latency


def test_sentinel_config_has_peak_tracking_interval():
    """SentinelConfig should have peak tracking interval."""
    from pause_monitor.config import SentinelConfig

    config = SentinelConfig()
    assert config.peak_tracking_seconds == 30  # Default: one buffer cycle
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_sentinel_config_has_pause_threshold tests/test_config.py::test_sentinel_config_has_peak_tracking_interval -v`
Expected: FAIL (fields don't exist)

**Step 3: Write minimal implementation**

Update `SentinelConfig` in `src/pause_monitor/config.py`:
```python
@dataclass
class SentinelConfig:
    """Sentinel timing configuration."""

    fast_interval_ms: int = 100
    slow_interval_ms: int = 1000  # Deprecated - will be removed
    ring_buffer_seconds: int = 30
    pause_threshold_ratio: float = 2.0  # Latency ratio to detect pause
    peak_tracking_seconds: int = 30  # Interval to update peak stress
```

Also update `Config.save()` to include new fields:
```python
sentinel.add("pause_threshold_ratio", self.sentinel.pause_threshold_ratio)
sentinel.add("peak_tracking_seconds", self.sentinel.peak_tracking_seconds)
```

And `Config.load()`:
```python
sentinel=SentinelConfig(
    fast_interval_ms=sentinel_data.get("fast_interval_ms", 100),
    slow_interval_ms=sentinel_data.get("slow_interval_ms", 1000),
    ring_buffer_seconds=sentinel_data.get("ring_buffer_seconds", 30),
    pause_threshold_ratio=sentinel_data.get("pause_threshold_ratio", 2.0),
    peak_tracking_seconds=sentinel_data.get("peak_tracking_seconds", 30),
),
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): add pause_threshold_ratio and peak_tracking_seconds"
```

---

## Phase 2: Refactor Daemon as Single Loop

**Note:** Tasks are ordered to resolve dependencies. Each task builds on the previous.

### Task 2.1: Add TierAction Enum and TierManager to Daemon

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

# Incident tracking
self._current_incident_id: str | None = None
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

### Task 2.2: Add Stress Calculation Method to Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

from pause_monitor.collector import PowermetricsResult


def test_daemon_calculate_stress_all_factors(tmp_path):
    """Daemon should calculate stress with all 7 factors from powermetrics."""
    config = Config()
    config._data_dir = tmp_path
    daemon = Daemon(config)

    pm_result = PowermetricsResult(
        cpu_pct=80.0,
        cpu_freq=3000,
        cpu_temp=85.0,
        throttled=True,
        gpu_pct=90.0,
        wakeups_per_sec=300.0,
        io_bytes_per_sec=50_000_000.0,  # 50 MB/s - should contribute to I/O stress
        top_processes=[{"name": "test", "pid": 123, "cpu_ms_per_s": 500.0}],
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
    assert stress.total > 0  # Total should be sum of factors
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
        StressBreakdown with all 7 factors
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
    if pm_result.wakeups_per_sec > 100:
        wakeups = int(min(10, (pm_result.wakeups_per_sec - 100) / 40))  # 100-500 -> 0-10

    # I/O stress (0-10 points)
    # Scale: 0-10 MB/s = 0, 10-100 MB/s = 0-10 points
    io = 0
    io_mb_per_sec = pm_result.io_bytes_per_sec / (1024 * 1024)
    if io_mb_per_sec > 10:
        io = int(min(10, (io_mb_per_sec - 10) / 9))  # 10-100 MB/s -> 0-10

    return StressBreakdown(
        load=load,
        memory=memory,
        thermal=thermal,
        latency=latency,
        io=io,
        gpu=gpu,
        wakeups=wakeups,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_calculate_stress_all_factors -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add stress calculation with all 7 factors"
```

---

### Task 2.3a: Add incident_id and peak_stress to Storage Schema

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
# tests/test_storage.py - add to existing file

def test_event_has_incident_id_and_peak_stress(tmp_path):
    """Event should support incident_id and peak_stress fields."""
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
        stress=StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=5, wakeups=1),
        culprits=["test_app"],
        event_dir=None,
        incident_id="abc-123",
        peak_stress=35,
    )

    event_id = insert_event(conn, event)
    assert event_id > 0

    events = get_events(conn, limit=1)
    assert len(events) == 1
    assert events[0].incident_id == "abc-123"
    assert events[0].peak_stress == 35

    conn.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_event_has_incident_id_and_peak_stress -v`
Expected: FAIL (Event has no incident_id or peak_stress fields)

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
    incident_id: str | None = None  # Links related events (e.g., escalation + recovery)
    peak_stress: int | None = None  # Peak stress during this event
```

**Step 4: Update the SCHEMA** - add columns to the `events` table in the SCHEMA string:
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
    incident_id     TEXT,
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
            culprits, event_dir, status, notes, incident_id, peak_stress
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            event.incident_id,
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
               culprits, event_dir, status, notes, incident_id, peak_stress
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
            incident_id=row[15],
            peak_stress=row[16],
        )
        for row in rows
    ]
```

**Step 7: Bump SCHEMA_VERSION** at top of file:
```python
SCHEMA_VERSION = 3  # Was 2, bump for incident_id and peak_stress columns
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py::test_event_has_incident_id_and_peak_stress -v`
Expected: PASS

**Step 9: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add incident_id and peak_stress to Event schema"
```

---

### Task 2.3b: Handle Tier Actions in Daemon with Incident Linking

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

    # Simulate tier 2 entry
    daemon._tier2_start_time = time.time() - 60  # Started 60s ago
    daemon._tier2_peak_stress = 35
    daemon._tier2_peak_breakdown = StressBreakdown(
        load=10, memory=8, thermal=5, latency=3, io=2, gpu=5, wakeups=2
    )
    daemon._tier2_peak_process = "stressful_app"
    daemon._current_incident_id = str(uuid.uuid4())

    # Handle tier2_exit
    from pause_monitor.sentinel import TierAction
    stress = StressBreakdown(load=5, memory=3, thermal=0, latency=0, io=0, gpu=2, wakeups=1)
    await daemon._handle_tier_action(TierAction.TIER2_EXIT, stress)

    # Verify event was written
    events = get_events(daemon._conn, limit=1)
    assert len(events) == 1
    assert events[0].peak_stress == 35
    assert events[0].incident_id == daemon._current_incident_id


@pytest.mark.asyncio
async def test_daemon_tier3_to_tier2_links_incident(tmp_path):
    """When exiting tier 3 to tier 2, incident_id should be preserved."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)
    await daemon._init_database()

    # Simulate being in tier 3 with an incident
    incident_id = str(uuid.uuid4())
    daemon._current_incident_id = incident_id
    daemon._tier3_start_time = time.time() - 120

    # Exit tier 3 (to tier 2)
    from pause_monitor.sentinel import TierAction
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=5, wakeups=1)
    await daemon._handle_tier_action(TierAction.TIER3_EXIT, stress)

    # Should start tier 2 tracking with same incident_id
    assert daemon._tier2_start_time is not None
    assert daemon._current_incident_id == incident_id
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_tier2_exit_writes_bookmark tests/test_daemon.py::test_daemon_tier3_to_tier2_links_incident -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add imports at top of `src/pause_monitor/daemon.py`:
```python
import uuid
from datetime import datetime
from pause_monitor.storage import (
    Event, insert_event, init_database,
    migrate_add_event_status, migrate_add_stress_columns,
)
```

Add to `Daemon.__init__`:
```python
# Tier 2 tracking
self._tier2_start_time: float | None = None
self._tier2_peak_stress: int = 0
self._tier2_peak_breakdown: StressBreakdown | None = None
self._tier2_peak_process: str | None = None

# Tier 3 tracking
self._tier3_start_time: float | None = None

# Peak tracking timer
self._last_peak_check: float = 0.0
```

Add `_init_database` method to `Daemon` (extracted from `start()` for testability):
```python
async def _init_database(self) -> None:
    """Initialize database connection and run migrations.

    Extracted from start() so tests can initialize DB without full daemon startup.
    """
    self.config.data_dir.mkdir(parents=True, exist_ok=True)
    init_database(self.config.db_path)
    self._conn = sqlite3.connect(self.config.db_path)
    migrate_add_event_status(self._conn)
    migrate_add_stress_columns(self._conn)
```

Add `_handle_tier_action` method to `Daemon`:
```python
async def _handle_tier_action(self, action: TierAction, stress: StressBreakdown) -> None:
    """Handle tier transition actions."""
    now = time.time()

    if action == TierAction.TIER2_ENTRY:
        self._tier2_start_time = now
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        self._tier2_peak_process = None
        self._current_incident_id = str(uuid.uuid4())
        self._last_peak_check = now
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.info("tier2_entered", stress=stress.total, incident_id=self._current_incident_id)

    elif action == TierAction.TIER2_PEAK:
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.info("tier2_new_peak", stress=stress.total)

    elif action == TierAction.TIER2_EXIT:
        if self._tier2_start_time:
            duration = now - self._tier2_start_time
            event = Event(
                timestamp=datetime.fromtimestamp(self._tier2_start_time),
                duration=duration,
                stress=self._tier2_peak_breakdown or stress,
                culprits=[self._tier2_peak_process] if self._tier2_peak_process else [],
                event_dir=None,  # Bookmarks don't have forensics
                status="unreviewed",
                incident_id=self._current_incident_id,
                peak_stress=self._tier2_peak_stress,
            )
            insert_event(self._conn, event)
            self.state.event_count += 1
            log.info("tier2_exited", duration=duration, peak=self._tier2_peak_stress)

        self._tier2_start_time = None
        self._tier2_peak_stress = 0
        self._tier2_peak_breakdown = None
        self._tier2_peak_process = None
        self._current_incident_id = None
        self.ring_buffer.clear_snapshots()

    elif action == TierAction.TIER3_ENTRY:
        # Escalating from tier 2 - keep incident_id
        self._tier3_start_time = now
        self.ring_buffer.snapshot_processes(trigger=action.value)
        log.warning("tier3_entered", stress=stress.total, incident_id=self._current_incident_id)

    elif action == TierAction.TIER3_EXIT:
        # De-escalating to tier 2 - start linked recovery tracking
        self._tier3_start_time = None
        # Start tier 2 tracking with same incident_id (for recovery period)
        self._tier2_start_time = now
        self._tier2_peak_stress = stress.total
        self._tier2_peak_breakdown = stress
        self._last_peak_check = now
        # incident_id is preserved - don't reset it
        log.info("tier3_exited", stress=stress.total, incident_id=self._current_incident_id)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon.py::test_daemon_handles_tier2_exit_writes_bookmark tests/test_daemon.py::test_daemon_tier3_to_tier2_links_incident -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): handle tier actions with incident linking"
```

---

### Task 2.4: Handle Pause Detection in Daemon

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

    # Add some samples to ring buffer
    for i in range(5):
        stress = StressBreakdown(
            load=10 + i, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0
        )
        daemon.ring_buffer.push(stress, tier=1)

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
        stress=peak_sample.stress if peak_sample else StressBreakdown(0, 0, 0, 0, 0, 0, 0),
        culprits=culprit_names,
        event_dir=str(event_dir),
        status="unreviewed",
        incident_id=self._current_incident_id,
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

### Task 2.5: Add Peak Tracking Timer

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

    # Simulate being in tier 2
    daemon._tier2_start_time = time.time() - 60
    daemon._tier2_peak_stress = 20
    daemon._last_peak_check = time.time() - 35  # 35 seconds ago

    # New stress is higher
    new_stress = StressBreakdown(load=15, memory=10, thermal=5, latency=3, io=2, gpu=5, wakeups=2)

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

    # Simulate being in tier 2
    daemon._tier2_start_time = time.time() - 60
    daemon._tier2_peak_stress = 50
    daemon._last_peak_check = time.time() - 10  # Only 10 seconds ago

    # New stress is lower
    new_stress = StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=0, gpu=5, wakeups=0)

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
        # Get top process from latest powermetrics data
        if self._latest_pm_result and self._latest_pm_result.top_processes:
            self._tier2_peak_process = self._latest_pm_result.top_processes[0]["name"]
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

### Task 2.6: Create Main Loop Method in Daemon

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

    # Track samples pushed to ring buffer
    pushed_samples = []
    original_push = daemon.ring_buffer.push

    def track_push(stress, tier):
        pushed_samples.append((stress, tier))
        return original_push(stress, tier)

    monkeypatch.setattr(daemon.ring_buffer, "push", track_push)

    # Mock powermetrics to yield two samples then stop
    samples = [
        PowermetricsResult(
            cpu_pct=50.0,
            cpu_freq=3000,
            cpu_temp=60.0,
            throttled=False,
            gpu_pct=30.0,
            wakeups_per_sec=100.0,
            io_bytes_per_sec=0.0,
            top_processes=[{"name": "test", "pid": 1, "cpu_ms_per_s": 100}],
        ),
        PowermetricsResult(
            cpu_pct=80.0,
            cpu_freq=3000,
            cpu_temp=70.0,
            throttled=True,
            gpu_pct=60.0,
            wakeups_per_sec=200.0,
            io_bytes_per_sec=0.0,
            top_processes=[{"name": "test", "pid": 1, "cpu_ms_per_s": 200}],
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

Add method to `Daemon`:
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

                # Push to ring buffer
                self.ring_buffer.push(stress, tier=current_tier)

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

### Task 2.7: Update Daemon.start() to Use Main Loop

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

## Phase 3: Add Socket Server

### Task 3.1: Create SocketServer Class

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

    # Add samples
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=15, wakeups=3)
    buffer.push(stress, tier=1)

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
    """Unix domain socket server for real-time ring buffer streaming.

    Broadcasts ring buffer state to connected clients at 10Hz.
    Protocol: newline-delimited JSON messages.
    """

    def __init__(
        self,
        socket_path: Path,
        ring_buffer: RingBuffer,
        broadcast_interval_ms: int = 100,
    ):
        self.socket_path = socket_path
        self.ring_buffer = ring_buffer
        self.broadcast_interval_ms = broadcast_interval_ms
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._running = False
        self._broadcast_task: asyncio.Task | None = None

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
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        log.info("socket_server_started", path=str(self.socket_path))

    async def stop(self) -> None:
        """Stop the socket server."""
        self._running = False

        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

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

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection."""
        self._clients.add(writer)
        log.info("socket_client_connected", count=len(self._clients))

        try:
            # Send initial data immediately
            await self._send_to_client(writer)

            # Keep connection alive until client disconnects
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

    async def _broadcast_loop(self) -> None:
        """Broadcast ring buffer data to all clients at interval."""
        while self._running:
            await asyncio.sleep(self.broadcast_interval_ms / 1000.0)

            for writer in list(self._clients):
                try:
                    await self._send_to_client(writer)
                except Exception:
                    self._clients.discard(writer)

    async def _send_to_client(self, writer: asyncio.StreamWriter) -> None:
        """Send current ring buffer state to a client."""
        samples = self.ring_buffer.samples
        latest = samples[-1] if samples else None

        message: SocketMessage = {
            "samples": [
                SampleDict(
                    timestamp=s.timestamp,
                    stress=StressDict(**asdict(s.stress)),
                    tier=s.tier,
                )
                for s in samples[-30:]  # Last 3 seconds
            ],
            "tier": latest.tier if latest else 1,
            "current_stress": StressDict(**asdict(latest.stress)) if latest else None,
            "sample_count": len(samples),
        }

        data = json.dumps(message) + "\n"
        writer.write(data.encode())
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

### Task 3.2: Integrate SocketServer into Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `src/pause_monitor/config.py`

**Step 1: Add socket_path to Config**

```python
# src/pause_monitor/config.py - add to Config class

@property
def socket_path(self) -> Path:
    """Path to daemon Unix socket."""
    return self.data_dir / "daemon.sock"
```

**Step 2: Add SocketServer to Daemon**

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
# Start socket server for TUI
self._socket_server = SocketServer(
    socket_path=self.config.socket_path,
    ring_buffer=self.ring_buffer,
    broadcast_interval_ms=self.config.sentinel.fast_interval_ms,
)
await self._socket_server.start()
```

Update `Daemon.stop()`:
```python
if self._socket_server:
    await self._socket_server.stop()
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add src/pause_monitor/daemon.py src/pause_monitor/config.py
git commit -m "feat(daemon): integrate socket server for TUI"
```

---

## Phase 4: Update TUI to Use Socket

### Task 4.1: Create SocketClient Class

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
    """SocketClient should receive and dispatch data."""
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
        received = []

        client = SocketClient(socket_path=socket_path)
        client.on_data = lambda data: received.append(data)

        await client.connect()
        await asyncio.sleep(0.2)

        assert len(received) >= 1
        assert received[0]["tier"] == 2

        await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()
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

    Connects to daemon socket and dispatches received data via callback.
    """

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._read_task: asyncio.Task | None = None
        self.on_data: Callable[[dict[str, Any]], None] | None = None

    @property
    def connected(self) -> bool:
        """Whether client is connected."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect to the daemon socket."""
        if not self.socket_path.exists():
            raise FileNotFoundError(f"Socket not found: {self.socket_path}")

        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self.socket_path)
        )
        self._running = True
        self._read_task = asyncio.create_task(self._read_loop())
        log.info("socket_client_connected", path=str(self.socket_path))

    async def disconnect(self) -> None:
        """Disconnect from the daemon socket."""
        self._running = False

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        log.info("socket_client_disconnected")

    async def _read_loop(self) -> None:
        """Read data from socket and dispatch to callback."""
        if not self._reader:
            return

        try:
            while self._running:
                line = await self._reader.readline()
                if not line:
                    break

                try:
                    data = json.loads(line.decode())
                    if self.on_data:
                        self.on_data(data)
                except json.JSONDecodeError:
                    log.warning("invalid_json_from_daemon")
        except asyncio.CancelledError:
            pass
        except ConnectionError:
            log.warning("socket_connection_lost")
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

### Task 4.2: Update TUI to Connect via Socket

**Files:**
- Modify: `src/pause_monitor/tui/app.py`

**Step 1: Add socket client import and attribute**

Add imports at top of `src/pause_monitor/tui/app.py`:
```python
from typing import Any
from pause_monitor.socket_client import SocketClient
```

Add to `PauseMonitorApp.__init__`:
```python
self._socket_client: SocketClient | None = None
self._use_socket = False  # True when connected via socket
```

**Step 2: Update on_mount to try socket first**

Replace or update the `on_mount` method:
```python
async def on_mount(self) -> None:
    """Initialize on startup."""
    self.title = "pause-monitor"
    self.sub_title = "System Health Monitor"

    # Try socket connection first (real-time 10Hz)
    socket_path = self.config.socket_path
    if socket_path.exists():
        try:
            self._socket_client = SocketClient(socket_path)
            self._socket_client.on_data = self._handle_socket_data
            await self._socket_client.connect()
            self._use_socket = True
            self.sub_title = "System Health Monitor (live)"
            log.info("tui_connected_via_socket")
            return
        except Exception as e:
            log.warning("socket_connection_failed", error=str(e))

    # Fall back to SQLite polling
    self._setup_sqlite_fallback()
```

**Step 3: Add SQLite fallback method**

```python
def _setup_sqlite_fallback(self) -> None:
    """Set up SQLite-based polling (fallback when daemon not running)."""
    from pause_monitor.storage import get_connection, get_schema_version

    if not self.config.db_path.exists():
        self.notify(
            "Daemon not running. Start with: sudo pause-monitor daemon",
            severity="warning",
        )
        return

    self._conn = get_connection(self.config.db_path)

    if get_schema_version(self._conn) == 0:
        self._conn.close()
        self._conn = None
        self.notify("Database not initialized.", severity="error")
        return

    self.sub_title = "System Health Monitor (history only)"
    self.set_interval(1.0, self._refresh_data)
    self._refresh_data()
```

**Step 4: Add socket data handler**

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

**Step 5: Update on_unmount**

```python
async def on_unmount(self) -> None:
    """Clean up on shutdown."""
    if self._socket_client:
        await self._socket_client.disconnect()
    if self._conn:
        self._conn.close()
```

**Step 6: Manual testing**

```bash
# Terminal 1: Start daemon (needs sudo for powermetrics)
sudo uv run pause-monitor daemon

# Terminal 2: Start TUI
uv run pause-monitor tui
# Should show "(live)" in subtitle
```

**Step 7: Commit**

```bash
git add src/pause_monitor/tui/app.py
git commit -m "feat(tui): connect via socket for real-time data"
```

---

## Phase 5: Cleanup

### Task 5.1: Remove Sentinel Loops and Migrate to TierManager-Only

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Modify: `tests/test_sentinel.py`

**Step 1: Keep TierManager, Tier enum, remove Sentinel class methods**

Update `src/pause_monitor/sentinel.py` to remove the loop methods from `Sentinel` class:

1. **Keep these classes/enums:**
   - `Tier` enum
   - `TierManager` class (unchanged)

2. **Modify `Sentinel` class to be a thin wrapper or deprecate it:**

```python
class Sentinel:
    """DEPRECATED: Use Daemon's main loop instead.

    This class is kept for backwards compatibility but loops are removed.
    The TierManager can be accessed directly from Daemon.tier_manager.
    """

    def __init__(
        self,
        buffer: RingBuffer,
        fast_interval: float = 0.1,
        slow_interval: float = 1.0,
        elevated_threshold: int = 15,
        critical_threshold: int = 50,
        on_tier_change: Callable[[Tier], None] | None = None,
        on_pause_detected: Callable[[float], None] | None = None,
    ):
        """Initialize Sentinel (deprecated - use Daemon instead)."""
        self.buffer = buffer
        self.fast_interval = fast_interval
        self.slow_interval = slow_interval
        self.tier_manager = TierManager(
            elevated_threshold=elevated_threshold,
            critical_threshold=critical_threshold,
        )
        self._running = False
        self._core_count = os.cpu_count() or 1
        self.on_tier_change = on_tier_change
        self.on_pause_detected = on_pause_detected

    def stop(self) -> None:
        """Stop the sentinel."""
        self._running = False

    async def start(self) -> None:
        """DEPRECATED: Start method removed. Use Daemon._main_loop() instead."""
        raise DeprecationWarning(
            "Sentinel.start() is deprecated. Use Daemon with main loop instead."
        )
```

**Delete these methods:**
- `_fast_loop`
- `_slow_loop`
- `_calculate_fast_stress`
- `_handle_tier_action`
- `_handle_potential_pause`

**Step 2: Update tests**

Update `tests/test_sentinel.py`:
- Remove tests for `_fast_loop`, `_slow_loop`
- Keep tests for `TierManager` functionality
- Add test for deprecation warning on `Sentinel.start()`

```python
# tests/test_sentinel.py - add

def test_sentinel_start_raises_deprecation():
    """Sentinel.start() should raise DeprecationWarning."""
    from pause_monitor.sentinel import Sentinel
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=10)
    sentinel = Sentinel(buffer=buffer)

    with pytest.raises(DeprecationWarning):
        import asyncio
        asyncio.run(sentinel.start())
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_sentinel.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/test_sentinel.py
git commit -m "refactor(sentinel): remove loops, deprecate class, keep TierManager"
```

---

### Task 5.2: Remove SamplePolicy and slow_interval_ms ✅ COMPLETED (Pre-Implementation Cleanup)

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `src/pause_monitor/config.py`
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_collector.py`
- Modify: `tests/test_daemon.py`

**Step 1: Remove dead code from collector.py**

Delete these classes/functions from `src/pause_monitor/collector.py`:
- `SamplePolicy` class
- `SamplingState` class
- `PolicyResult` class
- `_get_io_counters` function (stub returning (0, 0) - I/O now comes from powermetrics)
- `_get_network_counters` function (stub returning (0, 0) - not used)

Also remove any calls to these functions (around lines 55-58 in `collect_sample`).

**Step 2: Remove slow_interval_ms from config entirely**

Update `SentinelConfig` in `src/pause_monitor/config.py`:
```python
@dataclass
class SentinelConfig:
    """Sentinel timing configuration."""

    fast_interval_ms: int = 100
    # slow_interval_ms removed - powermetrics always runs at fast_interval_ms
    ring_buffer_seconds: int = 30
    pause_threshold_ratio: float = 2.0
    peak_tracking_seconds: int = 30
```

**Explicit locations to update:**

1. `src/pause_monitor/config.py` — Remove `slow_interval_ms` field from `SentinelConfig` dataclass
2. `src/pause_monitor/config.py` — Remove `slow_interval_ms` from `Config.save()` in the sentinel section
3. `src/pause_monitor/config.py` — Remove `slow_interval_ms` from `Config.load()` sentinel parsing
4. `src/pause_monitor/daemon.py` — Remove `slow_interval` parameter from Sentinel constructor call (if present)
5. `src/pause_monitor/sentinel.py` — Remove `slow_interval` parameter from `Sentinel.__init__()` and `self.slow_interval` assignment

Also search tests for any references to `slow_interval_ms` and remove them.

**Step 3: Remove self.policy from Daemon if present**

Search `src/pause_monitor/daemon.py` for `self.policy` and remove any references.

**Step 4: Update tests that reference removed classes**

Remove or update tests in `tests/test_collector.py` and `tests/test_daemon.py` that reference `SamplePolicy`.

**Step 5: Run tests**

Run: `uv run pytest -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/collector.py src/pause_monitor/config.py src/pause_monitor/daemon.py tests/
git commit -m "refactor: remove orphaned SamplePolicy, deprecate slow_interval_ms"
```

---

### Task 5.3: Remove Old Daemon._run_loop ✅ COMPLETED (Pre-Implementation Cleanup)

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Delete the old _run_loop method if it exists**

Search for `def _run_loop` in `src/pause_monitor/daemon.py` and delete it entirely. It's replaced by `_main_loop`.

**Step 2: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "refactor(daemon): remove old _run_loop"
```

---

### Task 5.4: Update Memories

**Files:**
- Modify: `.serena/memories/unimplemented_features.md`
- Modify: `.serena/memories/implementation_guide.md`

**Step 1: Update unimplemented_features**

Mark as completed:
```markdown
## Completed (via Redesign)

- ~~Sentinel slow loop~~ → Replaced by Daemon powermetrics integration
- ~~TUI socket streaming~~ → Implemented via SocketServer/SocketClient
- ~~Complete 7-factor stress~~ → All factors now calculated from powermetrics
- ~~Process attribution~~ → Using powermetrics top_processes
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
- `Daemon._calculate_stress()` - 7-factor stress from powermetrics
- `TierManager` - Tier state machine (extracted from Sentinel)
- `SocketServer` - Broadcasts ring buffer to TUI
- `SocketClient` - TUI receives real-time data

### Deprecated
- `Sentinel.start()` - Use Daemon main loop instead
- `SamplePolicy` - Removed, no longer needed
- `slow_interval_ms` config - Kept for compat, unused
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

4. **Tier 2 events create bookmarks with incident_id**
   ```bash
   # Generate stress, wait for tier 2 exit
   uv run pause-monitor events
   # Should show recent event with peak stress and incident_id
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
| 1 | 1.1-1.5 | Update PowermetricsStream for 100ms + complete data + failure handling + config |
| 2 | 2.1-2.7 | Refactor Daemon as single loop with tier handling, incident linking, peak tracking |
| 3 | 3.1-3.2 | Add Unix socket server |
| 4 | 4.1-4.2 | Update TUI to use socket client |
| 5 | 5.1-5.4 | Remove orphaned code, update docs |

**Total: 21 tasks**

**Key Architecture Changes:**
- powermetrics drives the main loop at 100ms (was 1000ms separate from sentinel)
- Ring buffer receives complete samples (was partial fast-path data)
- TUI streams from socket (was polling SQLite)
- SQLite stores only tier events (was storing all samples)
- Sentinel → deprecated, TierManager extracted to Daemon
- Incident linking via `incident_id` for related events
- Peak tracking every 30 seconds during elevated/critical periods
- Config-driven thresholds (pause_threshold_ratio, peak_tracking_seconds)
