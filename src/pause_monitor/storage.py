"""SQLite storage layer for pause-monitor."""

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from pause_monitor.collector import ProcessSamples

log = structlog.get_logger()

SCHEMA_VERSION = 8  # Per-process event tracking with process_events and process_snapshots


SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_process_events_pid_boot
    ON process_events(pid, boot_time);
CREATE INDEX IF NOT EXISTS idx_process_events_open
    ON process_events(exit_time) WHERE exit_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_process_snapshots_event
    ON process_snapshots(event_id);
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


def get_daemon_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a value from daemon_state table."""
    try:
        row = conn.execute("SELECT value FROM daemon_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def set_daemon_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a value in daemon_state table."""
    conn.execute(
        "INSERT OR REPLACE INTO daemon_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, time.time()),
    )
    conn.commit()


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
class ProcessSampleRecord:
    """Process sample record - ring buffer entry for 1Hz collection."""

    timestamp: float
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


def insert_process_sample(
    conn: sqlite3.Connection, timestamp: float, samples: ProcessSamples
) -> int:
    """Insert process sample into ring buffer.

    Args:
        conn: Database connection
        timestamp: Unix timestamp of the sample
        samples: ProcessSamples to serialize and store

    Returns:
        The ID of the inserted record
    """
    cursor = conn.execute(
        "INSERT INTO process_sample_records (timestamp, data) VALUES (?, ?)",
        (timestamp, samples.to_json()),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def get_process_samples(
    conn: sqlite3.Connection,
    start_time: float | None = None,
    end_time: float | None = None,
    limit: int = 1000,
) -> list[ProcessSampleRecord]:
    """Retrieve process samples from ring buffer, optionally filtered by time range.

    Args:
        conn: Database connection
        start_time: Optional start timestamp (inclusive)
        end_time: Optional end timestamp (inclusive)
        limit: Maximum records to return (default 1000)

    Returns:
        List of ProcessSampleRecord with deserialized data, ordered by timestamp
    """
    query = "SELECT id, timestamp, data FROM process_sample_records"
    params: list[float | int] = []
    conditions = []

    if start_time is not None:
        conditions.append("timestamp >= ?")
        params.append(start_time)
    if end_time is not None:
        conditions.append("timestamp <= ?")
        params.append(end_time)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        ProcessSampleRecord(
            id=row[0],
            timestamp=row[1],
            data=ProcessSamples.from_json(row[2]),
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

    Note: This is legacy code for the old events table which no longer exists.
    Kept for backward compatibility until full cleanup.

    Args:
        conn: Database connection
        event_id: The event ID to update
        status: New status (unreviewed, reviewed, pinned, dismissed)
        notes: Optional notes (if None, existing notes are preserved)

    Raises:
        ValueError: If status is not a valid event status
    """
    valid_statuses = frozenset({"unreviewed", "reviewed", "pinned", "dismissed"})
    if status not in valid_statuses:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {sorted(valid_statuses)}")
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

    # Note: process_sample_records is now a time-based ring buffer without event_id
    # Pruning is handled separately by prune_process_samples()

    # Delete events
    cursor = conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
    events_deleted = cursor.rowcount

    conn.commit()

    log.info("prune_complete", events_deleted=events_deleted)

    return events_deleted


# --- Process Event CRUD Functions ---


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
    result = cursor.lastrowid
    assert result is not None
    return result


def get_open_events(conn: sqlite3.Connection, boot_time: int) -> list[dict]:
    """Get all open events (no exit_time) for current boot."""
    cursor = conn.execute(
        """SELECT id, pid, command, entry_time, entry_band, peak_score, peak_band
           FROM process_events
           WHERE boot_time = ? AND exit_time IS NULL""",
        (boot_time,),
    )
    return [
        {
            "id": r[0],
            "pid": r[1],
            "command": r[2],
            "entry_time": r[3],
            "entry_band": r[4],
            "peak_score": r[5],
            "peak_band": r[6],
        }
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
