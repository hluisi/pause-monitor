# Per-Process Band Tracking Implementation Plan

> **For Claude:** Use systema:build to implement this plan task-by-task.

**Goal:** Replace tier-based system-wide event tracking with per-process band tracking, where individual processes crossing a configurable threshold create events with captured ProcessScore snapshots.

**Architecture:** ProcessScore is THE canonical data schema, self-contained with `captured_at` timestamp. The ring buffer stores batches of `list[ProcessScore]` per 1Hz collection cycle. A ProcessTracker monitors each ProcessScore against band thresholds — when a process crosses `tracking_band`, an event is created and snapshots are persisted to SQLite. Boot time detection ensures stale PIDs from previous boots are invalidated.

**Key Decisions:**
- Add `captured_at: float` to ProcessScore — makes it fully self-contained
- Boot time via `os.stat('/var/run').st_birthtime` — simple, macOS-native
- Bands referenced by name (`tracking_band`, `forensics_band`), not raw thresholds
- Delete tier system entirely — no wrapping, no migration, breaking change is fine
- TUI gets minimal updates to stay functional — validation tool, not design target

**Patterns to Follow:**
- Dataclass-based configuration (existing pattern in config.py)
- JSON serialization via `to_dict()`/`from_dict()` (existing pattern in ProcessScore)
- Direct function-based storage operations (existing pattern in storage.py)
- TDD always — test first, implement, verify, commit

**Tech Stack:** Python 3.14, SQLite, pytest, existing dependencies only

---

### Task 1: Add `captured_at` to ProcessScore

**Context:** ProcessScore needs to be self-contained with its own timestamp. This is foundational — everything else depends on it.

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Test: `tests/test_collector.py`

**Step 1: Write the failing test**

```python
def test_process_score_has_captured_at():
    """ProcessScore includes captured_at timestamp."""
    score = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000000,
        cmprs=0,
        pageins=10,
        csw=100,
        sysbsd=50,
        threads=4,
        score=45,
        categories=frozenset(["cpu"]),
        captured_at=1706000000.0,
    )
    assert score.captured_at == 1706000000.0


def test_process_score_to_dict_includes_captured_at():
    """to_dict() includes captured_at field."""
    score = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000000,
        cmprs=0,
        pageins=10,
        csw=100,
        sysbsd=50,
        threads=4,
        score=45,
        categories=frozenset(["cpu"]),
        captured_at=1706000000.0,
    )
    d = score.to_dict()
    assert d["captured_at"] == 1706000000.0


def test_process_score_from_dict_restores_captured_at():
    """from_dict() restores captured_at field."""
    d = {
        "pid": 123,
        "command": "test",
        "cpu": 50.0,
        "state": "running",
        "mem": 1000000,
        "cmprs": 0,
        "pageins": 10,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
        "score": 45,
        "categories": ["cpu"],
        "captured_at": 1706000000.0,
    }
    score = ProcessScore.from_dict(d)
    assert score.captured_at == 1706000000.0
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_collector.py::test_process_score_has_captured_at tests/test_collector.py::test_process_score_to_dict_includes_captured_at tests/test_collector.py::test_process_score_from_dict_restores_captured_at -v
```
Expected: FAIL with "unexpected keyword argument 'captured_at'"

**Step 3: Write minimal implementation**

Add `captured_at: float` field to ProcessScore dataclass, update `to_dict()` to include it, update `from_dict()` to restore it. Update `TopCollector._score_process()` to pass `time.time()` as `captured_at`. Update all test fixtures that create ProcessScore instances.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_collector.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add captured_at timestamp to ProcessScore"
```

---

### Task 2: Add BandsConfig

**Context:** Replace TiersConfig with band-based configuration. Bands define score ranges with names, plus `tracking_band` and `forensics_band` for behavior triggers.

**Files:**
- Modify: `src/pause_monitor/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_bands_config_defaults():
    """BandsConfig has sensible defaults."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    assert bands.low == 20
    assert bands.medium == 40
    assert bands.elevated == 60
    assert bands.high == 80
    assert bands.critical == 100
    assert bands.tracking_band == "elevated"
    assert bands.forensics_band == "high"


def test_bands_config_get_band_for_score():
    """get_band() returns correct band name for score."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    assert bands.get_band(0) == "low"
    assert bands.get_band(19) == "low"
    assert bands.get_band(20) == "medium"
    assert bands.get_band(39) == "medium"
    assert bands.get_band(40) == "elevated"
    assert bands.get_band(59) == "elevated"
    assert bands.get_band(60) == "high"
    assert bands.get_band(79) == "high"
    assert bands.get_band(80) == "critical"
    assert bands.get_band(100) == "critical"


def test_bands_config_get_threshold_for_band():
    """get_threshold() returns score threshold for band name."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    assert bands.get_threshold("low") == 0
    assert bands.get_threshold("medium") == 20
    assert bands.get_threshold("elevated") == 40
    assert bands.get_threshold("high") == 60
    assert bands.get_threshold("critical") == 80


def test_bands_config_tracking_threshold():
    """tracking_threshold property returns threshold for tracking_band."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    assert bands.tracking_threshold == 40


def test_bands_config_forensics_threshold():
    """forensics_threshold property returns threshold for forensics_band."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    assert bands.forensics_threshold == 60


def test_config_has_bands_not_tiers():
    """Config has bands attribute, not tiers."""
    from pause_monitor.config import Config

    config = Config()
    assert hasattr(config, "bands")
    assert not hasattr(config, "tiers")
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py::test_bands_config_defaults tests/test_config.py::test_bands_config_get_band_for_score tests/test_config.py::test_bands_config_get_threshold_for_band tests/test_config.py::test_bands_config_tracking_threshold tests/test_config.py::test_bands_config_forensics_threshold tests/test_config.py::test_config_has_bands_not_tiers -v
```
Expected: FAIL with "cannot import name 'BandsConfig'"

**Step 3: Write minimal implementation**

```python
@dataclass
class BandsConfig:
    """Band thresholds and behavior triggers."""
    low: int = 20
    medium: int = 40
    elevated: int = 60
    high: int = 80
    critical: int = 100
    tracking_band: str = "elevated"
    forensics_band: str = "high"

    def get_band(self, score: int) -> str:
        """Return band name for a given score."""
        if score >= self.critical:
            return "critical"
        if score >= self.high:
            return "high"
        if score >= self.elevated:
            return "elevated"
        if score >= self.medium:
            return "medium"
        return "low"

    def get_threshold(self, band: str) -> int:
        """Return the minimum score for a band."""
        thresholds = {
            "low": 0,
            "medium": self.low,
            "elevated": self.medium,
            "high": self.elevated,
            "critical": self.high,
        }
        return thresholds[band]

    @property
    def tracking_threshold(self) -> int:
        return self.get_threshold(self.tracking_band)

    @property
    def forensics_threshold(self) -> int:
        return self.get_threshold(self.forensics_band)
```

Replace `tiers: TiersConfig` with `bands: BandsConfig` in Config dataclass. Update `Config.load()` and `Config.save()`. Delete `TiersConfig` and `_load_tiers_config`.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_config.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): replace TiersConfig with BandsConfig"
```

---

### Task 3: Add boot time detection and daemon_state helpers

**Context:** We need to detect reboots to know when PIDs become stale. Boot time detection plus get/set functions for the existing `daemon_state` table.

**Files:**
- Create: `src/pause_monitor/boottime.py`
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_boottime.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
# tests/test_boottime.py

import time


def test_get_boot_time_returns_int():
    """get_boot_time() returns boot timestamp as int."""
    from pause_monitor.boottime import get_boot_time

    boot_time = get_boot_time()
    assert isinstance(boot_time, int)
    assert boot_time > 0


def test_get_boot_time_is_stable():
    """get_boot_time() returns same value on repeated calls."""
    from pause_monitor.boottime import get_boot_time

    t1 = get_boot_time()
    t2 = get_boot_time()
    assert t1 == t2


def test_get_boot_time_is_in_past():
    """Boot time should be before now."""
    from pause_monitor.boottime import get_boot_time

    boot_time = get_boot_time()
    assert boot_time < time.time()


# tests/test_storage.py (add these)

def test_get_daemon_state_missing_key(tmp_path):
    """get_daemon_state returns None for missing key."""
    from pause_monitor.storage import get_connection, init_db, get_daemon_state

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    value = get_daemon_state(conn, "nonexistent")
    assert value is None


def test_set_and_get_daemon_state(tmp_path):
    """set_daemon_state stores value, get_daemon_state retrieves it."""
    from pause_monitor.storage import get_connection, init_db, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    set_daemon_state(conn, "boot_time", "1706000000")
    value = get_daemon_state(conn, "boot_time")
    assert value == "1706000000"


def test_set_daemon_state_overwrites(tmp_path):
    """set_daemon_state overwrites existing value."""
    from pause_monitor.storage import get_connection, init_db, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    set_daemon_state(conn, "boot_time", "1000")
    set_daemon_state(conn, "boot_time", "2000")
    value = get_daemon_state(conn, "boot_time")
    assert value == "2000"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_boottime.py tests/test_storage.py::test_get_daemon_state_missing_key tests/test_storage.py::test_set_and_get_daemon_state tests/test_storage.py::test_set_daemon_state_overwrites -v
```
Expected: FAIL with "No module named 'pause_monitor.boottime'" and "cannot import name 'get_daemon_state'"

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/boottime.py
"""Boot time detection for macOS."""

import os


def get_boot_time() -> int:
    """Return system boot time as Unix timestamp."""
    return int(os.stat("/var/run").st_birthtime)


# src/pause_monitor/storage.py (add these functions)

def get_daemon_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a value from daemon_state table."""
    row = conn.execute(
        "SELECT value FROM daemon_state WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def set_daemon_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a value in daemon_state table."""
    conn.execute(
        "INSERT OR REPLACE INTO daemon_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, time.time()),
    )
    conn.commit()
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_boottime.py tests/test_storage.py::test_get_daemon_state_missing_key tests/test_storage.py::test_set_and_get_daemon_state tests/test_storage.py::test_set_daemon_state_overwrites -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/boottime.py tests/test_boottime.py src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat: add boot time detection and daemon_state helpers"
```

---

### Task 4: New database schema

**Context:** Replace old schema with new per-process event tables. Delete legacy tables. This is a breaking change — no migration needed.

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
def test_schema_has_process_events_table(tmp_path):
    """Schema includes process_events table."""
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_events'"
    )
    assert cursor.fetchone() is not None


def test_schema_has_process_snapshots_table(tmp_path):
    """Schema includes process_snapshots table."""
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_snapshots'"
    )
    assert cursor.fetchone() is not None


def test_schema_no_legacy_events_table(tmp_path):
    """Schema does not have legacy events table."""
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    )
    assert cursor.fetchone() is None


def test_process_events_table_structure(tmp_path):
    """process_events has expected columns."""
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    cursor = conn.execute("PRAGMA table_info(process_events)")
    columns = {row[1] for row in cursor.fetchall()}

    expected = {
        "id", "pid", "command", "boot_time",
        "entry_time", "exit_time", "entry_band", "peak_band",
        "peak_score", "peak_snapshot",
    }
    assert expected.issubset(columns)


def test_process_snapshots_table_structure(tmp_path):
    """process_snapshots has expected columns."""
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)
    cursor = conn.execute("PRAGMA table_info(process_snapshots)")
    columns = {row[1] for row in cursor.fetchall()}

    expected = {"id", "event_id", "snapshot_type", "snapshot"}
    assert expected.issubset(columns)
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_storage.py::test_schema_has_process_events_table tests/test_storage.py::test_schema_has_process_snapshots_table tests/test_storage.py::test_schema_no_legacy_events_table tests/test_storage.py::test_process_events_table_structure tests/test_storage.py::test_process_snapshots_table_structure -v
```
Expected: FAIL — tables don't exist

**Step 3: Write minimal implementation**

Update `SCHEMA` in storage.py to version 8:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS daemon_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS process_sample_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid INTEGER NOT NULL,
    command TEXT NOT NULL,
    boot_time INTEGER NOT NULL,
    entry_time REAL NOT NULL,
    exit_time REAL,
    entry_band TEXT NOT NULL,
    peak_band TEXT NOT NULL,
    peak_score INTEGER NOT NULL,
    peak_snapshot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    snapshot_type TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES process_events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_process_events_pid_boot ON process_events(pid, boot_time);
CREATE INDEX IF NOT EXISTS idx_process_events_open ON process_events(exit_time) WHERE exit_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_process_snapshots_event ON process_snapshots(event_id);
"""

CURRENT_SCHEMA_VERSION = 8
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_storage.py::test_schema_has_process_events_table tests/test_storage.py::test_schema_has_process_snapshots_table tests/test_storage.py::test_schema_no_legacy_events_table tests/test_storage.py::test_process_events_table_structure tests/test_storage.py::test_process_snapshots_table_structure -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): new schema v8 with process_events and process_snapshots"
```

---

### Task 5: Storage functions for process events

**Context:** CRUD operations for the new process_events and process_snapshots tables.

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

```python
def test_create_process_event(tmp_path):
    """create_process_event inserts and returns event ID."""
    from pause_monitor.storage import get_connection, init_db, create_process_event

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test_cmd",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot='{"pid": 123, "score": 45}',
    )

    assert event_id is not None
    assert isinstance(event_id, int)


def test_get_open_events(tmp_path):
    """get_open_events returns events with no exit_time."""
    from pause_monitor.storage import get_connection, init_db, create_process_event, get_open_events

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    create_process_event(
        conn, pid=123, command="open", boot_time=1706000000,
        entry_time=1706000100.5, entry_band="elevated",
        peak_score=45, peak_band="elevated", peak_snapshot="{}"
    )

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123


def test_close_process_event(tmp_path):
    """close_process_event sets exit_time."""
    from pause_monitor.storage import get_connection, init_db, create_process_event, close_process_event, get_open_events

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    event_id = create_process_event(
        conn, pid=123, command="test", boot_time=1706000000,
        entry_time=1706000100.5, entry_band="elevated",
        peak_score=45, peak_band="elevated", peak_snapshot="{}"
    )

    close_process_event(conn, event_id, exit_time=1706000200.5)

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 0


def test_update_process_event_peak(tmp_path):
    """update_process_event_peak updates peak fields."""
    from pause_monitor.storage import get_connection, init_db, create_process_event, update_process_event_peak

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    event_id = create_process_event(
        conn, pid=123, command="test", boot_time=1706000000,
        entry_time=1706000100.5, entry_band="elevated",
        peak_score=45, peak_band="elevated", peak_snapshot='{"score": 45}'
    )

    update_process_event_peak(conn, event_id, peak_score=80, peak_band="critical", peak_snapshot='{"score": 80}')

    row = conn.execute("SELECT peak_score, peak_band FROM process_events WHERE id = ?", (event_id,)).fetchone()
    assert row[0] == 80
    assert row[1] == "critical"


def test_insert_process_snapshot(tmp_path):
    """insert_process_snapshot adds snapshot to event."""
    from pause_monitor.storage import get_connection, init_db, create_process_event, insert_process_snapshot

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    event_id = create_process_event(
        conn, pid=123, command="test", boot_time=1706000000,
        entry_time=1706000100.5, entry_band="elevated",
        peak_score=45, peak_band="elevated", peak_snapshot="{}"
    )

    insert_process_snapshot(conn, event_id, snapshot_type="entry", snapshot='{"score": 45}')

    row = conn.execute(
        "SELECT snapshot_type, snapshot FROM process_snapshots WHERE event_id = ?",
        (event_id,)
    ).fetchone()
    assert row[0] == "entry"
    assert row[1] == '{"score": 45}'
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_storage.py::test_create_process_event tests/test_storage.py::test_get_open_events tests/test_storage.py::test_close_process_event tests/test_storage.py::test_update_process_event_peak tests/test_storage.py::test_insert_process_snapshot -v
```
Expected: FAIL with "cannot import name 'create_process_event'"

**Step 3: Write minimal implementation**

```python
def create_process_event(
    conn: sqlite3.Connection,
    pid: int,
    command: str,
    boot_time: int,
    entry_time: float,
    entry_band: str,
    peak_score: int,
    peak_band: str,
    peak_snapshot: str,
) -> int:
    """Create a new process event. Returns event ID."""
    cursor = conn.execute(
        """INSERT INTO process_events
           (pid, command, boot_time, entry_time, entry_band, peak_score, peak_band, peak_snapshot)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, command, boot_time, entry_time, entry_band, peak_score, peak_band, peak_snapshot),
    )
    conn.commit()
    return cursor.lastrowid


def get_open_events(conn: sqlite3.Connection, boot_time: int) -> list[dict]:
    """Get all open events (no exit_time) for current boot."""
    cursor = conn.execute(
        """SELECT id, pid, command, entry_time, entry_band, peak_score, peak_band
           FROM process_events
           WHERE boot_time = ? AND exit_time IS NULL""",
        (boot_time,),
    )
    return [
        {"id": r[0], "pid": r[1], "command": r[2], "entry_time": r[3],
         "entry_band": r[4], "peak_score": r[5], "peak_band": r[6]}
        for r in cursor.fetchall()
    ]


def close_process_event(conn: sqlite3.Connection, event_id: int, exit_time: float) -> None:
    """Close an event by setting exit_time."""
    conn.execute(
        "UPDATE process_events SET exit_time = ? WHERE id = ?",
        (exit_time, event_id),
    )
    conn.commit()


def update_process_event_peak(
    conn: sqlite3.Connection,
    event_id: int,
    peak_score: int,
    peak_band: str,
    peak_snapshot: str,
) -> None:
    """Update peak score/band/snapshot for an event."""
    conn.execute(
        "UPDATE process_events SET peak_score = ?, peak_band = ?, peak_snapshot = ? WHERE id = ?",
        (peak_score, peak_band, peak_snapshot, event_id),
    )
    conn.commit()


def insert_process_snapshot(
    conn: sqlite3.Connection,
    event_id: int,
    snapshot_type: str,
    snapshot: str,
) -> None:
    """Insert a snapshot for an event."""
    conn.execute(
        "INSERT INTO process_snapshots (event_id, snapshot_type, snapshot) VALUES (?, ?, ?)",
        (event_id, snapshot_type, snapshot),
    )
    conn.commit()
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_storage.py::test_create_process_event tests/test_storage.py::test_get_open_events tests/test_storage.py::test_close_process_event tests/test_storage.py::test_update_process_event_peak tests/test_storage.py::test_insert_process_snapshot -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add process event CRUD functions"
```

---

### Task 6: ProcessTracker class

**Context:** Core tracking logic. Monitors ProcessScores against band thresholds, manages event lifecycle per PID.

**Files:**
- Create: `src/pause_monitor/tracker.py`
- Test: `tests/test_tracker.py`

**Step 1: Write the failing test**

```python
# tests/test_tracker.py

import json


def test_tracker_creates_event_on_threshold_crossing(tmp_path):
    """ProcessTracker creates event when score crosses tracking threshold."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.tracker import ProcessTracker
    from pause_monitor.storage import get_connection, init_db, get_open_events
    from pause_monitor.collector import ProcessScore

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    bands = BandsConfig()  # tracking_band="elevated", threshold=40
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Score below threshold — no event
    score_low = ProcessScore(
        pid=123, command="test", cpu=10.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=30, categories=frozenset(), captured_at=1706000100.0
    )
    tracker.update([score_low])
    assert len(get_open_events(conn, 1706000000)) == 0

    # Score above threshold — event created
    score_high = ProcessScore(
        pid=123, command="test", cpu=50.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=50, categories=frozenset(["cpu"]), captured_at=1706000101.0
    )
    tracker.update([score_high])
    events = get_open_events(conn, 1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123


def test_tracker_closes_event_when_score_drops(tmp_path):
    """ProcessTracker closes event when score drops below threshold."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.tracker import ProcessTracker
    from pause_monitor.storage import get_connection, init_db, get_open_events
    from pause_monitor.collector import ProcessScore

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    score_high = ProcessScore(
        pid=123, command="test", cpu=50.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=50, categories=frozenset(["cpu"]), captured_at=1706000100.0
    )
    tracker.update([score_high])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Exit bad state
    score_low = ProcessScore(
        pid=123, command="test", cpu=10.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=30, categories=frozenset(), captured_at=1706000200.0
    )
    tracker.update([score_low])
    assert len(get_open_events(conn, 1706000000)) == 0


def test_tracker_updates_peak(tmp_path):
    """ProcessTracker updates peak when score increases."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.tracker import ProcessTracker
    from pause_monitor.storage import get_connection, init_db
    from pause_monitor.collector import ProcessScore

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter at 50
    score1 = ProcessScore(
        pid=123, command="test", cpu=50.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=50, categories=frozenset(["cpu"]), captured_at=1706000100.0
    )
    tracker.update([score1])

    # Peak at 80
    score2 = ProcessScore(
        pid=123, command="test", cpu=80.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=80, categories=frozenset(["cpu"]), captured_at=1706000101.0
    )
    tracker.update([score2])

    row = conn.execute("SELECT peak_score, peak_band FROM process_events WHERE pid = 123").fetchone()
    assert row[0] == 80
    assert row[1] == "critical"


def test_tracker_closes_missing_pids(tmp_path):
    """ProcessTracker closes events for PIDs no longer in scores."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.tracker import ProcessTracker
    from pause_monitor.storage import get_connection, init_db, get_open_events
    from pause_monitor.collector import ProcessScore

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # PID 123 enters bad state
    score = ProcessScore(
        pid=123, command="test", cpu=50.0, state="running",
        mem=1000, cmprs=0, pageins=0, csw=10, sysbsd=5, threads=2,
        score=50, categories=frozenset(["cpu"]), captured_at=1706000100.0
    )
    tracker.update([score])
    assert len(get_open_events(conn, 1706000000)) == 1

    # PID 123 disappears from scores (process ended or no longer selected)
    tracker.update([])
    assert len(get_open_events(conn, 1706000000)) == 0
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_tracker.py -v
```
Expected: FAIL with "No module named 'pause_monitor.tracker'"

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/tracker.py
"""Per-process band tracking."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from pause_monitor.collector import ProcessScore
from pause_monitor.config import BandsConfig
from pause_monitor.storage import (
    create_process_event,
    close_process_event,
    update_process_event_peak,
    insert_process_snapshot,
    get_open_events,
)


@dataclass
class TrackedProcess:
    """In-memory state for a tracked process."""
    event_id: int
    pid: int
    peak_score: int


class ProcessTracker:
    """Tracks per-process band state and manages event lifecycle."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        bands: BandsConfig,
        boot_time: int,
    ) -> None:
        self.conn = conn
        self.bands = bands
        self.boot_time = boot_time
        self.tracked: dict[int, TrackedProcess] = {}
        self._restore_open_events()

    def _restore_open_events(self) -> None:
        """Restore tracking state from open events in DB."""
        for event in get_open_events(self.conn, self.boot_time):
            self.tracked[event["pid"]] = TrackedProcess(
                event_id=event["id"],
                pid=event["pid"],
                peak_score=event["peak_score"],
            )

    def update(self, scores: list[ProcessScore]) -> None:
        """Update tracking with new scores."""
        current_pids = {s.pid for s in scores}
        threshold = self.bands.tracking_threshold

        # Close events for PIDs no longer present
        for pid in list(self.tracked.keys()):
            if pid not in current_pids:
                self._close_event(pid, scores[0].captured_at if scores else 0.0)

        # Process each score
        for score in scores:
            band = self.bands.get_band(score.score)
            in_bad_state = score.score >= threshold

            if score.pid in self.tracked:
                # Already tracking — update peak or close
                tracked = self.tracked[score.pid]
                if in_bad_state:
                    if score.score > tracked.peak_score:
                        self._update_peak(score)
                else:
                    self._close_event(score.pid, score.captured_at)
            else:
                # Not tracking — maybe start
                if in_bad_state:
                    self._open_event(score)

    def _open_event(self, score: ProcessScore) -> None:
        """Create new event for process entering bad state."""
        snapshot_json = json.dumps(score.to_dict())
        band = self.bands.get_band(score.score)

        event_id = create_process_event(
            self.conn,
            pid=score.pid,
            command=score.command,
            boot_time=self.boot_time,
            entry_time=score.captured_at,
            entry_band=band,
            peak_score=score.score,
            peak_band=band,
            peak_snapshot=snapshot_json,
        )

        insert_process_snapshot(self.conn, event_id, "entry", snapshot_json)

        self.tracked[score.pid] = TrackedProcess(
            event_id=event_id,
            pid=score.pid,
            peak_score=score.score,
        )

    def _close_event(self, pid: int, exit_time: float) -> None:
        """Close event for process exiting bad state."""
        if pid not in self.tracked:
            return

        tracked = self.tracked.pop(pid)
        close_process_event(self.conn, tracked.event_id, exit_time)

    def _update_peak(self, score: ProcessScore) -> None:
        """Update peak for tracked process."""
        tracked = self.tracked[score.pid]
        tracked.peak_score = score.score

        snapshot_json = json.dumps(score.to_dict())
        band = self.bands.get_band(score.score)

        update_process_event_peak(
            self.conn,
            tracked.event_id,
            peak_score=score.score,
            peak_band=band,
            peak_snapshot=snapshot_json,
        )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_tracker.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/tracker.py tests/test_tracker.py
git commit -m "feat(tracker): add ProcessTracker for per-process band tracking"
```

---

### Task 7: Delete tier system

**Context:** Remove all tier-related code now that ProcessTracker replaces it. This is cleanup.

**Files:**
- Modify: `src/pause_monitor/sentinel.py` (delete TierManager, Tier, TierAction)
- Modify: `src/pause_monitor/daemon.py` (remove tier references)
- Modify: `src/pause_monitor/config.py` (delete TiersConfig if not already done)
- Delete: `tests/test_tier_manager.py`
- Modify: Other files with tier imports

**Step 1: Write the failing test**

```python
# tests/test_no_tiers.py

def test_no_tier_imports():
    """Codebase has no tier imports."""
    import ast
    from pathlib import Path

    src_dir = Path("src/pause_monitor")
    tier_names = {"Tier", "TierAction", "TierManager", "TiersConfig"}

    for py_file in src_dir.glob("**/*.py"):
        content = py_file.read_text()
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in tier_names:
                    raise AssertionError(f"Found {node.id} in {py_file}")
                if isinstance(node, ast.Attribute) and node.attr in tier_names:
                    raise AssertionError(f"Found {node.attr} in {py_file}")
        except SyntaxError:
            pass  # Skip files with syntax errors


def test_sentinel_has_no_tier_classes():
    """sentinel.py does not export Tier, TierAction, or TierManager."""
    import pause_monitor.sentinel as sentinel

    assert not hasattr(sentinel, "Tier")
    assert not hasattr(sentinel, "TierAction")
    assert not hasattr(sentinel, "TierManager")
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_no_tiers.py -v
```
Expected: FAIL — tier classes still exist

**Step 3: Write minimal implementation**

Delete `Tier`, `TierAction`, `TierManager` from sentinel.py. Update all imports and references in daemon.py and other files. Delete `tests/test_tier_manager.py`.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_no_tiers.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete tier system, replaced by ProcessTracker"
```

---

### Task 8: Integrate ProcessTracker into daemon

**Context:** Wire ProcessTracker into the daemon's main loop. Initialize with boot time on startup.

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
def test_daemon_initializes_tracker(tmp_path, mocker):
    """Daemon creates ProcessTracker on startup."""
    from pause_monitor.daemon import PauseMonitorDaemon
    from pause_monitor.config import Config
    from pause_monitor.storage import get_connection, init_db

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    config = Config()

    mocker.patch("pause_monitor.daemon.get_boot_time", return_value=1706000000)
    mocker.patch("pause_monitor.daemon.get_connection", return_value=conn)

    daemon = PauseMonitorDaemon(config)

    assert daemon.tracker is not None
    assert daemon.boot_time == 1706000000
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_daemon.py::test_daemon_initializes_tracker -v
```
Expected: FAIL — tracker attribute doesn't exist

**Step 3: Write minimal implementation**

Add ProcessTracker initialization to daemon:

```python
from pause_monitor.boottime import get_boot_time
from pause_monitor.tracker import ProcessTracker

# In __init__:
self.boot_time = get_boot_time()
self.tracker = ProcessTracker(self.conn, self.config.bands, self.boot_time)

# In main loop after collecting scores:
self.tracker.update(scores)
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_daemon.py::test_daemon_initializes_tracker -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): integrate ProcessTracker into main loop"
```

---

### Task 9: Update TUI for minimal functionality

**Context:** TUI needs minimal updates to not crash with new data model. This is NOT a redesign — just enough to validate the data layer works.

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Modify: `src/pause_monitor/tui/screens/*.py` (as needed)

**Step 1: Write the failing test**

```python
def test_tui_app_starts_without_crash(tmp_path):
    """TUI app initializes without errors."""
    from pause_monitor.tui.app import PauseMonitorApp
    from pause_monitor.config import Config

    # Just verify it can be instantiated
    app = PauseMonitorApp(config=Config())
    assert app is not None
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_tui.py::test_tui_app_starts_without_crash -v
```
Expected: FAIL if TUI references removed tier code

**Step 3: Write minimal implementation**

Update TUI to use `config.bands` instead of `config.tiers`. Replace any `TierManager` references with band-based logic or remove them.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_tui.py::test_tui_app_starts_without_crash -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/tui/
git commit -m "fix(tui): update for bands config, remove tier references"
```

---

### Task 10: Clean up dead code and tests

**Context:** Final cleanup pass. Remove any remaining references to deleted functionality.

**Files:**
- All files with stale imports or references
- Delete: Any test files for removed functionality
- Modify: `tests/` to remove tier-related tests

**Step 1: Write the failing test**

```python
def test_full_test_suite_passes():
    """All tests pass with no errors."""
    import subprocess
    result = subprocess.run(
        ["uv", "run", "pytest", "-v", "--tb=short"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Tests failed:\n{result.stdout}\n{result.stderr}"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest -v
```
Expected: Some tests fail due to stale references

**Step 3: Write minimal implementation**

Fix all failing tests. Delete tests for removed functionality. Update imports.

**Step 4: Run test to verify it passes**

```bash
uv run pytest -v
```
Expected: PASS — all tests green

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: clean up dead code and fix remaining tests"
```
