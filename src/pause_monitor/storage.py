"""SQLite storage layer for pause-monitor."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from pause_monitor.collector import ProcessSamples
from pause_monitor.stress import StressBreakdown

log = structlog.get_logger()

SCHEMA_VERSION = 7  # Added JSON blob storage for process samples

# Valid event status values
VALID_EVENT_STATUSES = frozenset({"unreviewed", "reviewed", "pinned", "dismissed"})

SCHEMA = """
-- Escalation events (one row per tier 1 → elevated → tier 1 episode)
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    start_timestamp REAL NOT NULL,
    end_timestamp   REAL,              -- NULL if ongoing
    peak_stress     INTEGER,
    peak_tier       INTEGER,           -- Highest tier reached (2 or 3)
    status          TEXT DEFAULT 'unreviewed',
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_timestamp);

-- Event samples (captured during escalation events)
-- Tier 2: peaks only, Tier 3: every sample at 10Hz
CREATE TABLE IF NOT EXISTS event_samples (
    id              INTEGER PRIMARY KEY,
    event_id        INTEGER NOT NULL REFERENCES events(id),
    timestamp       REAL NOT NULL,
    tier            INTEGER NOT NULL,  -- 2=peak save, 3=continuous save
    -- Metrics from PowermetricsResult
    elapsed_ns      INTEGER,
    throttled       INTEGER,
    cpu_power       REAL,
    gpu_pct         REAL,
    gpu_power       REAL,
    io_read_per_s   REAL,
    io_write_per_s  REAL,
    wakeups_per_s   REAL,
    pageins_per_s   REAL,
    -- Stress breakdown (8 factors)
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER,
    stress_gpu      INTEGER,
    stress_wakeups  INTEGER,
    stress_pageins  INTEGER,
    -- Top 5 processes (JSON arrays)
    top_cpu_procs       TEXT,
    top_pagein_procs    TEXT,
    top_wakeup_procs    TEXT,
    top_diskio_procs    TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_samples_event ON event_samples(event_id);
CREATE INDEX IF NOT EXISTS idx_event_samples_timestamp ON event_samples(timestamp);

-- Daemon state (persisted across restarts)
CREATE TABLE IF NOT EXISTS daemon_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      REAL NOT NULL
);

-- Legacy tables kept for backward compatibility (not used by tier-based saving)
CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY,
    timestamp       REAL NOT NULL,
    interval        REAL NOT NULL,
    load_avg        REAL,
    mem_pressure    INTEGER,
    throttled       INTEGER,
    cpu_power       REAL,
    gpu_pct         REAL,
    gpu_power       REAL,
    io_read_per_s   REAL,
    io_write_per_s  REAL,
    wakeups_per_s   REAL,
    pageins_per_s   REAL,
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER,
    stress_gpu      INTEGER,
    stress_wakeups  INTEGER,
    stress_pageins  INTEGER
);

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

-- New v7: Process sample records with JSON blob storage
-- Stores ProcessSamples (scored processes) as a single JSON blob per tier/event
CREATE TABLE IF NOT EXISTS process_sample_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    tier            INTEGER NOT NULL,
    data            TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_process_sample_records_event ON process_sample_records(event_id);
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


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a database connection."""
    return sqlite3.connect(db_path)


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    try:
        row = conn.execute("SELECT value FROM daemon_state WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


@dataclass
class Sample:
    """Single metrics sample - matches Data Dictionary exactly."""

    timestamp: datetime
    interval: float  # elapsed_ns / 1e9

    # System metrics (not from powermetrics)
    load_avg: float | None  # os.getloadavg()[0]
    mem_pressure: int | None  # sysctl kern.memorystatus_level (0-100)

    # From PowermetricsResult
    throttled: bool | None
    cpu_power: float | None
    gpu_pct: float | None
    gpu_power: float | None
    io_read_per_s: float | None
    io_write_per_s: float | None
    wakeups_per_s: float | None
    pageins_per_s: float | None  # CRITICAL for pause detection

    # Computed stress breakdown (includes stress_pageins)
    stress: StressBreakdown


def insert_sample(conn: sqlite3.Connection, sample: Sample) -> int:
    """Insert a sample and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO samples (
            timestamp, interval, load_avg, mem_pressure,
            throttled, cpu_power, gpu_pct, gpu_power,
            io_read_per_s, io_write_per_s, wakeups_per_s, pageins_per_s,
            stress_total, stress_load, stress_memory, stress_thermal,
            stress_latency, stress_io, stress_gpu, stress_wakeups, stress_pageins
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.timestamp.timestamp(),
            sample.interval,
            sample.load_avg,
            sample.mem_pressure,
            int(sample.throttled) if sample.throttled is not None else None,
            sample.cpu_power,
            sample.gpu_pct,
            sample.gpu_power,
            sample.io_read_per_s,
            sample.io_write_per_s,
            sample.wakeups_per_s,
            sample.pageins_per_s,
            sample.stress.total,
            sample.stress.load,
            sample.stress.memory,
            sample.stress.thermal,
            sample.stress.latency,
            sample.stress.io,
            sample.stress.gpu,
            sample.stress.wakeups,
            sample.stress.pageins,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_samples(conn: sqlite3.Connection, limit: int = 100) -> list[Sample]:
    """Get most recent samples."""
    rows = conn.execute(
        """
        SELECT timestamp, interval, load_avg, mem_pressure,
               throttled, cpu_power, gpu_pct, gpu_power,
               io_read_per_s, io_write_per_s, wakeups_per_s, pageins_per_s,
               stress_total, stress_load, stress_memory, stress_thermal,
               stress_latency, stress_io, stress_gpu, stress_wakeups, stress_pageins
        FROM samples ORDER BY timestamp DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        Sample(
            timestamp=datetime.fromtimestamp(row[0]),
            interval=row[1],
            load_avg=row[2],
            mem_pressure=row[3],
            throttled=bool(row[4]) if row[4] is not None else None,
            cpu_power=row[5],
            gpu_pct=row[6],
            gpu_power=row[7],
            io_read_per_s=row[8],
            io_write_per_s=row[9],
            wakeups_per_s=row[10],
            pageins_per_s=row[11],
            stress=StressBreakdown(
                load=row[13] or 0,
                memory=row[14] or 0,
                thermal=row[15] or 0,
                latency=row[16] or 0,
                io=row[17] or 0,
                gpu=row[18] or 0,
                wakeups=row[19] or 0,
                pageins=row[20] or 0,
            ),
        )
        for row in rows
    ]


@dataclass
class Event:
    """Escalation event (tier 1 → elevated → tier 1 episode)."""

    start_timestamp: datetime
    end_timestamp: datetime | None = None  # NULL if ongoing
    peak_stress: int | None = None
    peak_tier: int | None = None  # Highest tier reached (2 or 3)
    status: str = "unreviewed"
    notes: str | None = None
    id: int | None = None


@dataclass
class EventSample:
    """Sample captured during an escalation event."""

    event_id: int
    timestamp: datetime
    tier: int  # 2=peak save, 3=continuous save

    # Metrics from PowermetricsResult
    elapsed_ns: int
    throttled: bool
    cpu_power: float | None
    gpu_pct: float | None
    gpu_power: float | None
    io_read_per_s: float
    io_write_per_s: float
    wakeups_per_s: float
    pageins_per_s: float

    # Stress breakdown
    stress: StressBreakdown

    # Top 5 processes (already parsed from JSON)
    top_cpu_procs: list[dict]
    top_pagein_procs: list[dict]
    top_wakeup_procs: list[dict]
    top_diskio_procs: list[dict]

    id: int | None = None


@dataclass
class ProcessSampleRecord:
    """Process sample record with JSON blob storage (v7)."""

    event_id: int
    tier: int
    data: ProcessSamples
    id: int | None = None


def create_event(conn: sqlite3.Connection, start_timestamp: datetime) -> int:
    """Create a new event and return its ID."""
    cursor = conn.execute(
        "INSERT INTO events (start_timestamp) VALUES (?)",
        (start_timestamp.timestamp(),),
    )
    conn.commit()
    return cursor.lastrowid


def finalize_event(
    conn: sqlite3.Connection,
    event_id: int,
    end_timestamp: datetime,
    peak_stress: int,
    peak_tier: int,
) -> None:
    """Finalize an event when returning to tier 1."""
    conn.execute(
        """
        UPDATE events SET end_timestamp = ?, peak_stress = ?, peak_tier = ?
        WHERE id = ?
        """,
        (end_timestamp.timestamp(), peak_stress, peak_tier, event_id),
    )
    conn.commit()


def insert_event_sample(conn: sqlite3.Connection, sample: EventSample) -> int:
    """Insert an event sample and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO event_samples (
            event_id, timestamp, tier,
            elapsed_ns, throttled, cpu_power, gpu_pct, gpu_power,
            io_read_per_s, io_write_per_s, wakeups_per_s, pageins_per_s,
            stress_total, stress_load, stress_memory, stress_thermal,
            stress_latency, stress_io, stress_gpu, stress_wakeups, stress_pageins,
            top_cpu_procs, top_pagein_procs, top_wakeup_procs, top_diskio_procs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.event_id,
            sample.timestamp.timestamp(),
            sample.tier,
            sample.elapsed_ns,
            int(sample.throttled),
            sample.cpu_power,
            sample.gpu_pct,
            sample.gpu_power,
            sample.io_read_per_s,
            sample.io_write_per_s,
            sample.wakeups_per_s,
            sample.pageins_per_s,
            sample.stress.total,
            sample.stress.load,
            sample.stress.memory,
            sample.stress.thermal,
            sample.stress.latency,
            sample.stress.io,
            sample.stress.gpu,
            sample.stress.wakeups,
            sample.stress.pageins,
            json.dumps(sample.top_cpu_procs),
            json.dumps(sample.top_pagein_procs),
            json.dumps(sample.top_wakeup_procs),
            json.dumps(sample.top_diskio_procs),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_events(
    conn: sqlite3.Connection,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    status: str | None = None,
) -> list[Event]:
    """Get events, optionally filtered by time range and/or status."""
    query = """
        SELECT id, start_timestamp, end_timestamp, peak_stress, peak_tier, status, notes
        FROM events
    """
    params: list = []
    conditions = []

    if start:
        conditions.append("start_timestamp >= ?")
        params.append(start.timestamp())
    if end:
        conditions.append("start_timestamp <= ?")
        params.append(end.timestamp())
    if status:
        conditions.append("status = ?")
        params.append(status)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY start_timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        Event(
            id=row[0],
            start_timestamp=datetime.fromtimestamp(row[1]),
            end_timestamp=datetime.fromtimestamp(row[2]) if row[2] else None,
            peak_stress=row[3],
            peak_tier=row[4],
            status=row[5] or "unreviewed",
            notes=row[6],
        )
        for row in rows
    ]


def get_event_by_id(conn: sqlite3.Connection, event_id: int) -> Event | None:
    """Get a single event by ID."""
    row = conn.execute(
        """
        SELECT id, start_timestamp, end_timestamp, peak_stress, peak_tier, status, notes
        FROM events WHERE id = ?
        """,
        (event_id,),
    ).fetchone()

    if not row:
        return None

    return Event(
        id=row[0],
        start_timestamp=datetime.fromtimestamp(row[1]),
        end_timestamp=datetime.fromtimestamp(row[2]) if row[2] else None,
        peak_stress=row[3],
        peak_tier=row[4],
        status=row[5] or "unreviewed",
        notes=row[6],
    )


def get_event_samples(conn: sqlite3.Connection, event_id: int) -> list[EventSample]:
    """Get all samples for an event."""
    rows = conn.execute(
        """
        SELECT id, event_id, timestamp, tier,
               elapsed_ns, throttled, cpu_power, gpu_pct, gpu_power,
               io_read_per_s, io_write_per_s, wakeups_per_s, pageins_per_s,
               stress_total, stress_load, stress_memory, stress_thermal,
               stress_latency, stress_io, stress_gpu, stress_wakeups, stress_pageins,
               top_cpu_procs, top_pagein_procs, top_wakeup_procs, top_diskio_procs
        FROM event_samples WHERE event_id = ? ORDER BY timestamp
        """,
        (event_id,),
    ).fetchall()

    return [
        EventSample(
            id=row[0],
            event_id=row[1],
            timestamp=datetime.fromtimestamp(row[2]),
            tier=row[3],
            elapsed_ns=row[4] or 0,
            throttled=bool(row[5]),
            cpu_power=row[6],
            gpu_pct=row[7],
            gpu_power=row[8],
            io_read_per_s=row[9] or 0.0,
            io_write_per_s=row[10] or 0.0,
            wakeups_per_s=row[11] or 0.0,
            pageins_per_s=row[12] or 0.0,
            stress=StressBreakdown(
                load=row[14] or 0,
                memory=row[15] or 0,
                thermal=row[16] or 0,
                latency=row[17] or 0,
                io=row[18] or 0,
                gpu=row[19] or 0,
                wakeups=row[20] or 0,
                pageins=row[21] or 0,
            ),
            top_cpu_procs=json.loads(row[22]) if row[22] else [],
            top_pagein_procs=json.loads(row[23]) if row[23] else [],
            top_wakeup_procs=json.loads(row[24]) if row[24] else [],
            top_diskio_procs=json.loads(row[25]) if row[25] else [],
        )
        for row in rows
    ]


def insert_process_sample(
    conn: sqlite3.Connection, event_id: int, tier: int, samples: ProcessSamples
) -> int:
    """Insert process sample as JSON blob.

    Args:
        conn: Database connection
        event_id: The event ID to associate with
        tier: Tier level (2=peak save, 3=continuous save)
        samples: ProcessSamples to serialize and store

    Returns:
        The ID of the inserted record
    """
    cursor = conn.execute(
        "INSERT INTO process_sample_records (event_id, tier, data) VALUES (?, ?, ?)",
        (event_id, tier, samples.to_json()),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def get_process_samples(conn: sqlite3.Connection, event_id: int) -> list[ProcessSampleRecord]:
    """Retrieve and deserialize process samples for an event.

    Args:
        conn: Database connection
        event_id: The event ID to get samples for

    Returns:
        List of ProcessSampleRecord with deserialized data
    """
    rows = conn.execute(
        """SELECT id, event_id, tier, data
        FROM process_sample_records WHERE event_id = ? ORDER BY id""",
        (event_id,),
    ).fetchall()

    return [
        ProcessSampleRecord(
            id=row[0],
            event_id=row[1],
            tier=row[2],
            data=ProcessSamples.from_json(row[3]),
        )
        for row in rows
    ]


def update_event_status(
    conn: sqlite3.Connection,
    event_id: int,
    status: str,
    notes: str | None = None,
) -> None:
    """Update event status and optionally notes.

    Args:
        conn: Database connection
        event_id: The event ID to update
        status: New status (unreviewed, reviewed, pinned, dismissed)
        notes: Optional notes (if None, existing notes are preserved)

    Raises:
        ValueError: If status is not a valid event status
    """
    if status not in VALID_EVENT_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {sorted(VALID_EVENT_STATUSES)}"
        )
    if notes is not None:
        conn.execute(
            "UPDATE events SET status = ?, notes = ? WHERE id = ?",
            (status, notes, event_id),
        )
    else:
        conn.execute(
            "UPDATE events SET status = ? WHERE id = ?",
            (status, event_id),
        )
    conn.commit()


def prune_old_data(
    conn: sqlite3.Connection,
    events_days: int = 90,
) -> int:
    """Delete old events and their samples, respecting event lifecycle status.

    Only prunes events with status 'reviewed' or 'dismissed'.
    Never prunes 'unreviewed' or 'pinned' events regardless of age.

    Args:
        conn: Database connection
        events_days: Delete events older than this (only reviewed/dismissed)

    Returns:
        Number of events deleted

    Raises:
        ValueError: If retention days < 1
    """
    if events_days < 1:
        raise ValueError("Retention days must be >= 1")

    cutoff_events = time.time() - (events_days * 86400)

    # Get event IDs to delete
    event_ids = conn.execute(
        """
        SELECT id FROM events
        WHERE start_timestamp < ? AND status IN ('reviewed', 'dismissed')
        """,
        (cutoff_events,),
    ).fetchall()

    if not event_ids:
        return 0

    ids = [row[0] for row in event_ids]
    placeholders = ",".join("?" * len(ids))

    # Delete event samples first (foreign key)
    conn.execute(f"DELETE FROM event_samples WHERE event_id IN ({placeholders})", ids)

    # Delete process sample records (v7 JSON blob storage)
    conn.execute(f"DELETE FROM process_sample_records WHERE event_id IN ({placeholders})", ids)

    # Delete events
    cursor = conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
    events_deleted = cursor.rowcount

    conn.commit()

    log.info("prune_complete", events_deleted=events_deleted)

    return events_deleted
