"""SQLite storage layer for pause-monitor."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from pause_monitor.stress import StressBreakdown

log = structlog.get_logger()

SCHEMA_VERSION = 1

# Valid event status values
VALID_EVENT_STATUSES = frozenset({"unreviewed", "reviewed", "pinned", "dismissed"})

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
    status          TEXT DEFAULT 'unreviewed',
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


def migrate_add_event_status(conn: sqlite3.Connection) -> None:
    """Add status and notes columns to events table if missing.

    Migration sets existing events to 'reviewed' (not 'unreviewed')
    since they are legacy events that existed before status tracking.
    """
    cursor = conn.execute("PRAGMA table_info(events)")
    columns = {row[1] for row in cursor.fetchall()}

    if "status" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN status TEXT DEFAULT 'reviewed'")
        log.info("migration_applied", migration="add_event_status")
    if "notes" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN notes TEXT")
        log.info("migration_applied", migration="add_event_notes")
    conn.commit()


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
                gpu=0,  # DB schema update in Task 4
                wakeups=0,  # DB schema update in Task 4
            ),
        )
        for row in rows
    ]


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


def insert_event(conn: sqlite3.Connection, event: Event) -> int:
    """Insert an event and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO events (
            timestamp, duration, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io, culprits, event_dir, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            event.status,
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
    status: str | None = None,
) -> list[Event]:
    """Get events, optionally filtered by time range and/or status."""
    query = """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, status, notes
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
                gpu=0,  # DB schema update in Task 4
                wakeups=0,  # DB schema update in Task 4
            ),
            culprits=json.loads(row[9]) if row[9] else [],
            event_dir=row[10],
            status=row[11] or "unreviewed",
            notes=row[12],
        )
        for row in rows
    ]


def get_event_by_id(conn: sqlite3.Connection, event_id: int) -> Event | None:
    """Get a single event by ID."""
    row = conn.execute(
        """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, status, notes
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
            gpu=0,  # DB schema update in Task 4
            wakeups=0,  # DB schema update in Task 4
        ),
        culprits=json.loads(row[9]) if row[9] else [],
        event_dir=row[10],
        status=row[11] or "unreviewed",
        notes=row[12],
    )


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
    samples_days: int = 30,
    events_days: int = 90,
) -> tuple[int, int]:
    """Delete old samples and events, respecting event lifecycle status.

    Only prunes events with status 'reviewed' or 'dismissed'.
    Never prunes 'unreviewed' or 'pinned' events regardless of age.

    Args:
        conn: Database connection
        samples_days: Delete samples older than this
        events_days: Delete events older than this (only reviewed/dismissed)

    Returns:
        Tuple of (samples_deleted, events_deleted)

    Raises:
        ValueError: If retention days < 1
    """
    if samples_days < 1 or events_days < 1:
        raise ValueError("Retention days must be >= 1")

    cutoff_samples = time.time() - (samples_days * 86400)
    cutoff_events = time.time() - (events_days * 86400)

    # Delete old process samples first (foreign key)
    conn.execute(
        """
        DELETE FROM process_samples
        WHERE sample_id IN (SELECT id FROM samples WHERE timestamp < ?)
        """,
        (cutoff_samples,),
    )

    # Delete old samples
    cursor = conn.execute(
        "DELETE FROM samples WHERE timestamp < ?",
        (cutoff_samples,),
    )
    samples_deleted = cursor.rowcount

    # Delete old events - only if status is 'reviewed' or 'dismissed'
    # Never prune 'unreviewed' (needs attention) or 'pinned' (kept forever)
    cursor = conn.execute(
        """
        DELETE FROM events
        WHERE timestamp < ? AND status IN ('reviewed', 'dismissed')
        """,
        (cutoff_events,),
    )
    events_deleted = cursor.rowcount

    conn.commit()

    log.info(
        "prune_complete",
        samples_deleted=samples_deleted,
        events_deleted=events_deleted,
    )

    return samples_deleted, events_deleted
