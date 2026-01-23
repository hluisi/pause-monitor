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

**Extracted to:** [`phase-3-daemon-refactor.md`](phase-3-daemon-refactor.md)

7 tasks covering TierAction enum, stress calculation, peak_stress storage, tier action handling, pause detection, peak tracking, and the main loop.

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
