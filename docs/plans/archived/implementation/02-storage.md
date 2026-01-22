# Part 2: Storage

> **Navigation:** [Index](./index.md) | [Prev: Foundation](./01-foundation.md) | **Current** | [Next: Collection](./03-collection.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 3-4 (Storage Layer + Storage Operations)
**Tasks:** 8-11
**Dependencies:** Part 1 (config.py, stress.py)

---

## Phase 3: Storage Layer

> **Note:** Storage is created before test fixtures because conftest.py needs to import from storage.

### Task 8: Database Schema

**Files:**
- Create: `src/pause_monitor/storage.py`
- Create: `tests/test_storage.py`

**Step 1: Write failing test for database initialization**

Create `tests/test_storage.py`:

```python
"""Tests for SQLite storage layer."""

import sqlite3
from pathlib import Path

import pytest

from pause_monitor.storage import init_database, get_schema_version, SCHEMA_VERSION


def test_init_database_creates_file(tmp_path: Path):
    """init_database creates SQLite file."""
    db_path = tmp_path / "test.db"
    init_database(db_path)
    assert db_path.exists()


def test_init_database_enables_wal(tmp_path: Path):
    """init_database enables WAL journal mode."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    result = conn.execute("PRAGMA journal_mode").fetchone()
    conn.close()
    assert result[0] == "wal"


def test_init_database_creates_tables(tmp_path: Path):
    """init_database creates required tables."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()

    table_names = [t[0] for t in tables]
    assert "samples" in table_names
    assert "process_samples" in table_names
    assert "events" in table_names
    assert "daemon_state" in table_names


def test_init_database_sets_schema_version(tmp_path: Path):
    """init_database sets schema version in daemon_state."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    version = get_schema_version(conn)
    conn.close()
    assert version == SCHEMA_VERSION
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_init_database_creates_file -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement storage module with schema**

Create `src/pause_monitor/storage.py`:

```python
"""SQLite storage layer for pause-monitor."""

import sqlite3
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 1

SCHEMA = """
-- Periodic samples (one row per sample interval)
CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY,
    timestamp       REAL NOT NULL,
    interval        REAL NOT NULL,
    cpu_pct         REAL,
    load_avg        REAL,
    mem_available   INTEGER,
    swap_used       INTEGER,
    io_read         INTEGER,
    io_write        INTEGER,
    net_sent        INTEGER,
    net_recv        INTEGER,
    cpu_temp        REAL,
    cpu_freq        INTEGER,
    throttled       INTEGER,
    gpu_pct         REAL,
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON samples(timestamp);

-- Per-process snapshots (linked to samples)
CREATE TABLE IF NOT EXISTS process_samples (
    id              INTEGER PRIMARY KEY,
    sample_id       INTEGER NOT NULL REFERENCES samples(id),
    pid             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    cpu_pct         REAL,
    mem_pct         REAL,
    io_read         INTEGER,
    io_write        INTEGER,
    energy_impact   REAL,
    is_suspect      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_process_samples_sample_id ON process_samples(sample_id);

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
    culprits        TEXT,
    event_dir       TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- Daemon state (persisted across restarts)
CREATE TABLE IF NOT EXISTS daemon_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      REAL NOT NULL
);
"""


def init_database(db_path: Path) -> None:
    """Initialize database with WAL mode and schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # WAL mode for concurrent reads
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA journal_size_limit=16777216")
        conn.execute("PRAGMA foreign_keys=ON")

        # Create schema
        conn.executescript(SCHEMA)

        # Set schema version
        conn.execute(
            "INSERT OR REPLACE INTO daemon_state (key, value, updated_at) VALUES (?, ?, ?)",
            ("schema_version", str(SCHEMA_VERSION), time.time()),
        )
        conn.commit()
        log.info("database_initialized", path=str(db_path), version=SCHEMA_VERSION)
    finally:
        conn.close()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    try:
        row = conn.execute(
            "SELECT value FROM daemon_state WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add SQLite schema and initialization"
```

---

### Task 9: Shared Test Fixtures

> **Note:** Now that storage exists, we can create conftest.py with fixtures that depend on it.

**Files:**
- Create: `tests/conftest.py`

**Step 1: Create conftest.py with fixtures**

Create `tests/conftest.py`:

```python
"""Shared test fixtures for pause-monitor."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.stress import StressBreakdown
from pause_monitor.storage import init_database


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(tmp_db: Path) -> Path:
    """Create an initialized database with schema."""
    init_database(tmp_db)
    return tmp_db


def create_test_stress() -> StressBreakdown:
    """Create a StressBreakdown for testing."""
    return StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)


@pytest.fixture
def sample_stress() -> StressBreakdown:
    """Fixture for a sample StressBreakdown."""
    return create_test_stress()
```

**Step 2: Verify fixtures work**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (4 tests, fixtures available)

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add shared fixtures in conftest.py"
```

---

## Phase 4: Storage Operations

### Task 10: Storage Sample Operations

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write failing tests for Sample dataclass and insert**

Add to `tests/test_storage.py`:

```python
from datetime import datetime
from conftest import create_test_stress


def test_sample_dataclass_fields():
    """Sample has correct fields matching design doc."""
    from pause_monitor.storage import Sample
    from pause_monitor.stress import StressBreakdown

    sample = Sample(
        timestamp=datetime.now(),
        interval=5.0,
        cpu_pct=25.5,
        load_avg=1.5,
        mem_available=8_000_000_000,
        swap_used=100_000_000,
        io_read=1_000_000,
        io_write=500_000,
        net_sent=10_000,
        net_recv=20_000,
        cpu_temp=65.0,
        cpu_freq=3000,
        throttled=False,
        gpu_pct=10.0,
        stress=StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0),
    )
    assert sample.cpu_pct == 25.5
    assert sample.stress.total == 15


def test_insert_sample(initialized_db: Path, sample_stress):
    """insert_sample stores sample in database."""
    from pause_monitor.storage import Sample, insert_sample

    sample = Sample(
        timestamp=datetime.now(),
        interval=5.0,
        cpu_pct=25.5,
        load_avg=1.5,
        mem_available=8_000_000_000,
        swap_used=100_000_000,
        io_read=1_000_000,
        io_write=500_000,
        net_sent=10_000,
        net_recv=20_000,
        cpu_temp=65.0,
        cpu_freq=3000,
        throttled=False,
        gpu_pct=10.0,
        stress=sample_stress,
    )

    conn = sqlite3.connect(initialized_db)
    sample_id = insert_sample(conn, sample)
    conn.close()

    assert sample_id > 0


def test_get_recent_samples(initialized_db: Path, sample_stress):
    """get_recent_samples returns samples in reverse chronological order."""
    from pause_monitor.storage import Sample, insert_sample, get_recent_samples
    import time

    conn = sqlite3.connect(initialized_db)

    for i in range(5):
        sample = Sample(
            timestamp=datetime.fromtimestamp(1000000 + i * 5),
            interval=5.0,
            cpu_pct=10.0 + i,
            load_avg=1.0,
            mem_available=8_000_000_000,
            swap_used=0,
            io_read=0,
            io_write=0,
            net_sent=0,
            net_recv=0,
            cpu_temp=None,
            cpu_freq=None,
            throttled=None,
            gpu_pct=None,
            stress=sample_stress,
        )
        insert_sample(conn, sample)

    samples = get_recent_samples(conn, limit=3)
    conn.close()

    assert len(samples) == 3
    assert samples[0].cpu_pct == 14.0  # Most recent
    assert samples[2].cpu_pct == 12.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_sample_dataclass_fields -v`
Expected: FAIL with ImportError

**Step 3: Implement Sample dataclass and operations**

Add to `src/pause_monitor/storage.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from pause_monitor.stress import StressBreakdown


@dataclass
class Sample:
    """Single metrics sample.

    Field names match design doc exactly.
    """

    timestamp: datetime
    interval: float
    cpu_pct: float | None
    load_avg: float | None
    mem_available: int | None
    swap_used: int | None
    io_read: int | None
    io_write: int | None
    net_sent: int | None
    net_recv: int | None
    cpu_temp: float | None
    cpu_freq: int | None
    throttled: bool | None
    gpu_pct: float | None
    stress: StressBreakdown


def insert_sample(conn: sqlite3.Connection, sample: Sample) -> int:
    """Insert a sample and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO samples (
            timestamp, interval, cpu_pct, load_avg, mem_available, swap_used,
            io_read, io_write, net_sent, net_recv, cpu_temp, cpu_freq,
            throttled, gpu_pct, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.timestamp.timestamp(),
            sample.interval,
            sample.cpu_pct,
            sample.load_avg,
            sample.mem_available,
            sample.swap_used,
            sample.io_read,
            sample.io_write,
            sample.net_sent,
            sample.net_recv,
            sample.cpu_temp,
            sample.cpu_freq,
            int(sample.throttled) if sample.throttled is not None else None,
            sample.gpu_pct,
            sample.stress.total,
            sample.stress.load,
            sample.stress.memory,
            sample.stress.thermal,
            sample.stress.latency,
            sample.stress.io,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_samples(conn: sqlite3.Connection, limit: int = 100) -> list[Sample]:
    """Get most recent samples."""
    rows = conn.execute(
        """
        SELECT timestamp, interval, cpu_pct, load_avg, mem_available, swap_used,
               io_read, io_write, net_sent, net_recv, cpu_temp, cpu_freq,
               throttled, gpu_pct, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io
        FROM samples ORDER BY timestamp DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        Sample(
            timestamp=datetime.fromtimestamp(row[0]),
            interval=row[1],
            cpu_pct=row[2],
            load_avg=row[3],
            mem_available=row[4],
            swap_used=row[5],
            io_read=row[6],
            io_write=row[7],
            net_sent=row[8],
            net_recv=row[9],
            cpu_temp=row[10],
            cpu_freq=row[11],
            throttled=bool(row[12]) if row[12] is not None else None,
            gpu_pct=row[13],
            stress=StressBreakdown(
                load=row[15] or 0,
                memory=row[16] or 0,
                thermal=row[17] or 0,
                latency=row[18] or 0,
                io=row[19] or 0,
            ),
        )
        for row in rows
    ]
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add Sample dataclass and insert/query operations"
```

---

### Task 11: Storage Event Operations

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write failing tests for Event operations**

Add to `tests/test_storage.py`:

```python
def test_event_dataclass():
    """Event has correct fields."""
    from pause_monitor.storage import Event

    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=create_test_stress(),
        culprits=["codemeter", "WindowServer"],
        event_dir="/path/to/events/12345",
        notes="Test pause",
    )
    assert event.duration == 3.5
    assert "codemeter" in event.culprits


def test_insert_event(initialized_db: Path, sample_stress):
    """insert_event stores event in database."""
    from pause_monitor.storage import Event, insert_event

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        notes=None,
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    conn.close()

    assert event_id > 0


def test_get_events_by_timerange(initialized_db: Path, sample_stress):
    """get_events returns events within time range."""
    from pause_monitor.storage import Event, insert_event, get_events

    conn = sqlite3.connect(initialized_db)

    base_time = 1000000.0
    for i in range(5):
        event = Event(
            timestamp=datetime.fromtimestamp(base_time + i * 3600),
            duration=1.0 + i,
            stress=sample_stress,
            culprits=[],
            event_dir=None,
            notes=None,
        )
        insert_event(conn, event)

    events = get_events(
        conn,
        start=datetime.fromtimestamp(base_time + 3600),
        end=datetime.fromtimestamp(base_time + 10800),
    )
    conn.close()

    assert len(events) == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_event_dataclass -v`
Expected: FAIL with ImportError

**Step 3: Implement Event dataclass and operations**

Add to `src/pause_monitor/storage.py`:

```python
import json


@dataclass
class Event:
    """Pause event record."""

    timestamp: datetime
    duration: float
    stress: StressBreakdown
    culprits: list[str]
    event_dir: str | None
    notes: str | None
    id: int | None = None


def insert_event(conn: sqlite3.Connection, event: Event) -> int:
    """Insert an event and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO events (
            timestamp, duration, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            json.dumps(event.culprits),
            event.event_dir,
            event.notes,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_events(
    conn: sqlite3.Connection,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> list[Event]:
    """Get events, optionally filtered by time range."""
    query = """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        FROM events
    """
    params: list = []

    if start or end:
        query += " WHERE "
        conditions = []
        if start:
            conditions.append("timestamp >= ?")
            params.append(start.timestamp())
        if end:
            conditions.append("timestamp <= ?")
            params.append(end.timestamp())
        query += " AND ".join(conditions)

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
            ),
            culprits=json.loads(row[9]) if row[9] else [],
            event_dir=row[10],
            notes=row[11],
        )
        for row in rows
    ]


def get_event_by_id(conn: sqlite3.Connection, event_id: int) -> Event | None:
    """Get a single event by ID."""
    row = conn.execute(
        """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        FROM events WHERE id = ?
        """,
        (event_id,),
    ).fetchone()

    if not row:
        return None

    return Event(
        id=row[0],
        timestamp=datetime.fromtimestamp(row[1]),
        duration=row[2],
        stress=StressBreakdown(
            load=row[4] or 0,
            memory=row[5] or 0,
            thermal=row[6] or 0,
            latency=row[7] or 0,
            io=row[8] or 0,
        ),
        culprits=json.loads(row[9]) if row[9] else [],
        event_dir=row[10],
        notes=row[11],
    )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add Event dataclass and query operations"
```

---

> **Next:** [Part 3: Collection](./03-collection.md)
