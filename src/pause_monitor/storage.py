"""SQLite storage layer for pause-monitor."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 8  # Per-process event tracking with process_events and process_snapshots


SCHEMA = """
CREATE TABLE IF NOT EXISTS daemon_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
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


class DatabaseNotAvailable(Exception):
    """Raised when database doesn't exist and command should exit gracefully."""

    pass


@contextmanager
def require_database(
    db_path: Path, *, exit_on_missing: bool = False
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for commands requiring database access.

    Handles database existence check and connection lifecycle.

    Args:
        db_path: Path to the database file
        exit_on_missing: If True, raise SystemExit(1) on missing database.
                        If False, raise DatabaseNotAvailable.

    Yields:
        sqlite3.Connection: Database connection

    Raises:
        DatabaseNotAvailable: If database doesn't exist and exit_on_missing is False
        SystemExit: If database doesn't exist and exit_on_missing is True
    """
    import click

    if not db_path.exists():
        if exit_on_missing:
            click.echo("Error: Database not found", err=True)
            raise SystemExit(1)
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        raise DatabaseNotAvailable()

    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


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


def prune_old_data(
    conn: sqlite3.Connection,
    events_days: int = 90,
) -> int:
    """Delete old closed process events.

    Args:
        conn: Database connection
        events_days: Delete closed process_events older than this

    Returns:
        Number of events deleted

    Raises:
        ValueError: If retention days < 1
    """
    if events_days < 1:
        raise ValueError("Retention days must be >= 1")

    cutoff_events = time.time() - (events_days * 86400)

    # Delete old closed process events (cascades to snapshots)
    cursor = conn.execute(
        """
        DELETE FROM process_events
        WHERE exit_time IS NOT NULL AND exit_time < ?
        """,
        (cutoff_events,),
    )
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


def get_process_events(
    conn: sqlite3.Connection,
    boot_time: int | None = None,
    time_cutoff: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get process events with optional filtering.

    Args:
        conn: Database connection
        boot_time: Filter to events from this boot (if None, gets all boots)
        time_cutoff: Filter to events with entry_time >= this value
        limit: Maximum number of events to return

    Returns:
        List of event dicts with id, pid, command, entry_time, exit_time,
        entry_band, peak_band, peak_score
    """
    base_query = """SELECT id, pid, command, entry_time, exit_time,
                           entry_band, peak_band, peak_score
                    FROM process_events"""
    conditions = []
    params: list = []

    if boot_time is not None:
        conditions.append("boot_time = ?")
        params.append(boot_time)

    if time_cutoff is not None:
        conditions.append("entry_time >= ?")
        params.append(time_cutoff)

    if conditions:
        base_query += " WHERE " + " AND ".join(conditions)

    base_query += " ORDER BY entry_time DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(base_query, params)
    return [
        {
            "id": r[0],
            "pid": r[1],
            "command": r[2],
            "entry_time": r[3],
            "exit_time": r[4],
            "entry_band": r[5],
            "peak_band": r[6],
            "peak_score": r[7],
        }
        for r in cursor.fetchall()
    ]


def get_process_event_detail(conn: sqlite3.Connection, event_id: int) -> dict | None:
    """Get detailed information for a single process event.

    Args:
        conn: Database connection
        event_id: The event ID to retrieve

    Returns:
        Event dict with all fields (including boot_time, peak_snapshot),
        or None if not found
    """
    row = conn.execute(
        """SELECT id, pid, command, boot_time, entry_time, exit_time,
                  entry_band, peak_band, peak_score, peak_snapshot
           FROM process_events WHERE id = ?""",
        (event_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "pid": row[1],
        "command": row[2],
        "boot_time": row[3],
        "entry_time": row[4],
        "exit_time": row[5],
        "entry_band": row[6],
        "peak_band": row[7],
        "peak_score": row[8],
        "peak_snapshot": row[9],
    }


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
