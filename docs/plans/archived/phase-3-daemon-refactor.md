# Phase 3: Refactor Daemon as Single Loop

Extracted from [`2026-01-21-pause-monitor-implementation.md`](2026-01-21-pause-monitor-implementation.md)

**Prerequisites:** Phase 1 (Unified Data Model) and Phase 2 (PowermetricsStream 100ms) — both complete.

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


**Goal:** Replace the Sentinel class with a single main loop in Daemon that processes powermetrics at 10Hz, calculates stress, manages tiers, and handles pause detection.

---

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
