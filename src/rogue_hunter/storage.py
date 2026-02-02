"""SQLite storage layer for rogue-hunter."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

import structlog

if TYPE_CHECKING:
    from rogue_hunter.collector import ProcessScore

log = structlog.get_logger()

SCHEMA_VERSION = 17  # Simplified: removed MetricValue low/high columns


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
    peak_snapshot_id INTEGER,
    FOREIGN KEY (peak_snapshot_id) REFERENCES process_snapshots(id)
);

CREATE TABLE IF NOT EXISTS process_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    snapshot_type TEXT NOT NULL,
    captured_at REAL NOT NULL,
    -- CPU
    cpu REAL NOT NULL,
    -- Memory
    mem INTEGER NOT NULL,
    mem_peak INTEGER NOT NULL,
    pageins INTEGER NOT NULL,
    pageins_rate REAL NOT NULL,
    faults INTEGER NOT NULL,
    faults_rate REAL NOT NULL,
    -- Disk I/O
    disk_io INTEGER NOT NULL,
    disk_io_rate REAL NOT NULL,
    -- Activity
    csw INTEGER NOT NULL,
    csw_rate REAL NOT NULL,
    syscalls INTEGER NOT NULL,
    syscalls_rate REAL NOT NULL,
    threads INTEGER NOT NULL,
    mach_msgs INTEGER NOT NULL,
    mach_msgs_rate REAL NOT NULL,
    -- Efficiency
    instructions INTEGER NOT NULL,
    cycles INTEGER NOT NULL,
    ipc REAL NOT NULL,
    -- Power
    energy INTEGER NOT NULL,
    energy_rate REAL NOT NULL,
    wakeups INTEGER NOT NULL,
    wakeups_rate REAL NOT NULL,
    -- Contention
    runnable_time INTEGER NOT NULL,
    runnable_time_rate REAL NOT NULL,
    qos_interactive INTEGER NOT NULL,
    qos_interactive_rate REAL NOT NULL,
    -- GPU
    gpu_time INTEGER NOT NULL,
    gpu_time_rate REAL NOT NULL,
    -- Zombie children
    zombie_children INTEGER NOT NULL,
    -- State
    state TEXT NOT NULL,
    priority INTEGER NOT NULL,
    -- Scoring (resource-based system)
    score INTEGER NOT NULL,
    band TEXT NOT NULL,
    cpu_share REAL NOT NULL,
    gpu_share REAL NOT NULL,
    mem_share REAL NOT NULL,
    disk_share REAL NOT NULL,
    wakeups_share REAL NOT NULL,
    disproportionality REAL NOT NULL,
    dominant_resource TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES process_events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_process_events_pid_boot
    ON process_events(pid, boot_time);
CREATE INDEX IF NOT EXISTS idx_process_events_open
    ON process_events(exit_time) WHERE exit_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_process_snapshots_event
    ON process_snapshots(event_id);
CREATE INDEX IF NOT EXISTS idx_process_snapshots_score
    ON process_snapshots(score);

-- Forensic captures linked to process events
CREATE TABLE IF NOT EXISTS forensic_captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    captured_at REAL NOT NULL,
    trigger TEXT NOT NULL,
    spindump_status TEXT,
    tailspin_status TEXT,
    logs_status TEXT,
    FOREIGN KEY (event_id) REFERENCES process_events(id) ON DELETE CASCADE
);

-- Process info from spindump
CREATE TABLE IF NOT EXISTS spindump_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    path TEXT,
    parent_pid INTEGER,
    parent_name TEXT,
    footprint_mb REAL,
    cpu_time_sec REAL,
    thread_count INTEGER,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Thread states from spindump
CREATE TABLE IF NOT EXISTS spindump_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    thread_name TEXT,
    sample_count INTEGER,
    priority INTEGER,
    cpu_time_sec REAL,
    state TEXT,
    blocked_on TEXT,
    FOREIGN KEY (process_id) REFERENCES spindump_processes(id) ON DELETE CASCADE
);

-- Log entries (from log show --style ndjson)
CREATE TABLE IF NOT EXISTS log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    mach_timestamp INTEGER,
    subsystem TEXT,
    category TEXT,
    process_name TEXT,
    process_id INTEGER,
    message_type TEXT,
    event_message TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Ring buffer context at capture time
CREATE TABLE IF NOT EXISTS buffer_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    peak_score INTEGER NOT NULL,
    culprits TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Indexes for forensic tables
CREATE INDEX IF NOT EXISTS idx_forensic_captures_event ON forensic_captures(event_id);
CREATE INDEX IF NOT EXISTS idx_spindump_processes_capture ON spindump_processes(capture_id);
CREATE INDEX IF NOT EXISTS idx_spindump_threads_process ON spindump_threads(process_id);
CREATE INDEX IF NOT EXISTS idx_log_entries_capture ON log_entries(capture_id);
CREATE INDEX IF NOT EXISTS idx_buffer_context_capture ON buffer_context(capture_id);
"""


def init_database(db_path: Path) -> None:
    """Initialize database with WAL mode and schema.

    If the database exists with a different schema version, it is deleted
    and recreated. No migrations - schema mismatch means fresh start.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Check existing database schema version
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            existing_version = _get_schema_version_raw(conn)
            if existing_version != SCHEMA_VERSION:
                log.info(
                    "schema_mismatch",
                    existing=existing_version,
                    expected=SCHEMA_VERSION,
                    action="recreate",
                )
                conn.close()
                db_path.unlink()
                # Also remove WAL and SHM files if they exist
                wal_path = db_path.with_suffix(".db-wal")
                shm_path = db_path.with_suffix(".db-shm")
                if wal_path.exists():
                    wal_path.unlink()
                if shm_path.exists():
                    shm_path.unlink()
            else:
                # Schema matches, nothing to do
                conn.close()
                return
        except sqlite3.OperationalError:
            # Corrupted or incompatible DB - delete and recreate
            conn.close()
            db_path.unlink()

    # Create fresh database
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


def _get_schema_version_raw(conn: sqlite3.Connection) -> int:
    """Get schema version without error handling (for init_database use)."""
    row = conn.execute("SELECT value FROM daemon_state WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a database connection with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
        click.echo("Database not found. Run 'rogue-hunter daemon' first.")
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
    """Delete old closed process events (cascades to forensic data).

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

    # Delete old closed process events (cascades to snapshots and forensic captures)
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
) -> int:
    """Create a new process event. Returns event ID.

    Note: peak_snapshot_id starts as NULL and must be set after inserting
    the entry snapshot via update_process_event_peak().
    """
    cursor = conn.execute(
        """INSERT INTO process_events
           (pid, command, boot_time, entry_time, entry_band, peak_score, peak_band)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (pid, command, boot_time, entry_time, entry_band, peak_score, peak_band),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def get_open_events(conn: sqlite3.Connection, boot_time: int) -> list[dict]:
    """Get all open events (no exit_time) for current boot."""
    cursor = conn.execute(
        """SELECT id, pid, command, entry_time, entry_band, peak_score, peak_band, peak_snapshot_id
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
            "peak_snapshot_id": r[7],
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
        Event dict with all fields including peak_snapshot (as MetricValue-compatible
        dict from joined process_snapshots row), or None if not found
    """
    # For simplicity, fetch the event and snapshot separately
    event_row = conn.execute(
        """SELECT id, pid, command, boot_time, entry_time, exit_time,
                  entry_band, peak_band, peak_score, peak_snapshot_id
           FROM process_events WHERE id = ?""",
        (event_id,),
    ).fetchone()

    if not event_row:
        return None

    peak_snapshot = None
    if event_row[9] is not None:
        snapshot = get_snapshot(conn, event_row[9])
        if snapshot:
            # Add captured_at at root level for compatibility
            peak_snapshot = snapshot

    return {
        "id": event_row[0],
        "pid": event_row[1],
        "command": event_row[2],
        "boot_time": event_row[3],
        "entry_time": event_row[4],
        "exit_time": event_row[5],
        "entry_band": event_row[6],
        "peak_band": event_row[7],
        "peak_score": event_row[8],
        "peak_snapshot_id": event_row[9],
        "peak_snapshot": peak_snapshot,
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
    peak_snapshot_id: int,
) -> None:
    """Update peak score/band/snapshot_id for an event."""
    conn.execute(
        """UPDATE process_events
           SET peak_score = ?, peak_band = ?, peak_snapshot_id = ?
           WHERE id = ?""",
        (peak_score, peak_band, peak_snapshot_id, event_id),
    )
    conn.commit()


def insert_process_snapshot(
    conn: sqlite3.Connection,
    event_id: int,
    snapshot_type: str,
    score: "ProcessScore",
) -> int:
    """Insert a snapshot for an event. Returns snapshot ID."""
    cursor = conn.execute(
        """INSERT INTO process_snapshots
           (event_id, snapshot_type, captured_at,
            cpu, mem, mem_peak, pageins, pageins_rate, faults, faults_rate,
            disk_io, disk_io_rate,
            csw, csw_rate, syscalls, syscalls_rate, threads, mach_msgs, mach_msgs_rate,
            instructions, cycles, ipc,
            energy, energy_rate, wakeups, wakeups_rate,
            runnable_time, runnable_time_rate, qos_interactive, qos_interactive_rate,
            gpu_time, gpu_time_rate,
            zombie_children,
            state, priority,
            score, band, cpu_share, gpu_share, mem_share, disk_share,
            wakeups_share, disproportionality, dominant_resource)
           VALUES (?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?,
                   ?, ?,
                   ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?,
                   ?,
                   ?, ?,
                   ?, ?, ?, ?, ?, ?,
                   ?, ?, ?)""",
        (
            event_id,
            snapshot_type,
            score.captured_at,
            # CPU
            score.cpu,
            # Memory
            score.mem,
            score.mem_peak,
            score.pageins,
            score.pageins_rate,
            score.faults,
            score.faults_rate,
            # Disk I/O
            score.disk_io,
            score.disk_io_rate,
            # Activity
            score.csw,
            score.csw_rate,
            score.syscalls,
            score.syscalls_rate,
            score.threads,
            score.mach_msgs,
            score.mach_msgs_rate,
            # Efficiency
            score.instructions,
            score.cycles,
            score.ipc,
            # Power
            score.energy,
            score.energy_rate,
            score.wakeups,
            score.wakeups_rate,
            # Contention
            score.runnable_time,
            score.runnable_time_rate,
            score.qos_interactive,
            score.qos_interactive_rate,
            # GPU
            score.gpu_time,
            score.gpu_time_rate,
            # Zombie children
            score.zombie_children,
            # State
            score.state,
            score.priority,
            # Scoring
            score.score,
            score.band,
            score.cpu_share,
            score.gpu_share,
            score.mem_share,
            score.disk_share,
            score.wakeups_share,
            score.disproportionality,
            score.dominant_resource,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    """Get a snapshot by ID."""
    row = conn.execute(
        """SELECT id, event_id, snapshot_type, captured_at,
                  cpu, mem, mem_peak, pageins, pageins_rate, faults, faults_rate,
                  disk_io, disk_io_rate,
                  csw, csw_rate, syscalls, syscalls_rate, threads, mach_msgs, mach_msgs_rate,
                  instructions, cycles, ipc,
                  energy, energy_rate, wakeups, wakeups_rate,
                  runnable_time, runnable_time_rate, qos_interactive, qos_interactive_rate,
                  gpu_time, gpu_time_rate,
                  zombie_children,
                  state, priority,
                  score, band, cpu_share, gpu_share, mem_share, disk_share,
                  wakeups_share, disproportionality, dominant_resource
           FROM process_snapshots WHERE id = ?""",
        (snapshot_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "event_id": row[1],
        "snapshot_type": row[2],
        "captured_at": row[3],
        # CPU
        "cpu": row[4],
        # Memory
        "mem": row[5],
        "mem_peak": row[6],
        "pageins": row[7],
        "pageins_rate": row[8],
        "faults": row[9],
        "faults_rate": row[10],
        # Disk I/O
        "disk_io": row[11],
        "disk_io_rate": row[12],
        # Activity
        "csw": row[13],
        "csw_rate": row[14],
        "syscalls": row[15],
        "syscalls_rate": row[16],
        "threads": row[17],
        "mach_msgs": row[18],
        "mach_msgs_rate": row[19],
        # Efficiency
        "instructions": row[20],
        "cycles": row[21],
        "ipc": row[22],
        # Power
        "energy": row[23],
        "energy_rate": row[24],
        "wakeups": row[25],
        "wakeups_rate": row[26],
        # Contention
        "runnable_time": row[27],
        "runnable_time_rate": row[28],
        "qos_interactive": row[29],
        "qos_interactive_rate": row[30],
        # GPU
        "gpu_time": row[31],
        "gpu_time_rate": row[32],
        # Zombie children
        "zombie_children": row[33],
        # State
        "state": row[34],
        "priority": row[35],
        # Scoring
        "score": row[36],
        "band": row[37],
        "cpu_share": row[38],
        "gpu_share": row[39],
        "mem_share": row[40],
        "disk_share": row[41],
        "wakeups_share": row[42],
        "disproportionality": row[43],
        "dominant_resource": row[44],
    }


def get_process_snapshots(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Get all snapshots for an event, ordered by capture time."""
    cursor = conn.execute(
        """SELECT id, event_id, snapshot_type, captured_at,
                  cpu, mem, mem_peak, pageins, pageins_rate, faults, faults_rate,
                  disk_io, disk_io_rate,
                  csw, csw_rate, syscalls, syscalls_rate, threads, mach_msgs, mach_msgs_rate,
                  instructions, cycles, ipc,
                  energy, energy_rate, wakeups, wakeups_rate,
                  runnable_time, runnable_time_rate, qos_interactive, qos_interactive_rate,
                  gpu_time, gpu_time_rate,
                  zombie_children,
                  state, priority,
                  score, band, cpu_share, gpu_share, mem_share, disk_share,
                  wakeups_share, disproportionality, dominant_resource
           FROM process_snapshots WHERE event_id = ?
           ORDER BY captured_at""",
        (event_id,),
    )
    return [
        {
            "id": r[0],
            "event_id": r[1],
            "snapshot_type": r[2],
            "captured_at": r[3],
            # CPU
            "cpu": r[4],
            # Memory
            "mem": r[5],
            "mem_peak": r[6],
            "pageins": r[7],
            "pageins_rate": r[8],
            "faults": r[9],
            "faults_rate": r[10],
            # Disk I/O
            "disk_io": r[11],
            "disk_io_rate": r[12],
            # Activity
            "csw": r[13],
            "csw_rate": r[14],
            "syscalls": r[15],
            "syscalls_rate": r[16],
            "threads": r[17],
            "mach_msgs": r[18],
            "mach_msgs_rate": r[19],
            # Efficiency
            "instructions": r[20],
            "cycles": r[21],
            "ipc": r[22],
            # Power
            "energy": r[23],
            "energy_rate": r[24],
            "wakeups": r[25],
            "wakeups_rate": r[26],
            # Contention
            "runnable_time": r[27],
            "runnable_time_rate": r[28],
            "qos_interactive": r[29],
            "qos_interactive_rate": r[30],
            # GPU
            "gpu_time": r[31],
            "gpu_time_rate": r[32],
            # Zombie children
            "zombie_children": r[33],
            # State
            "state": r[34],
            "priority": r[35],
            # Scoring
            "score": r[36],
            "band": r[37],
            "cpu_share": r[38],
            "gpu_share": r[39],
            "mem_share": r[40],
            "disk_share": r[41],
            "wakeups_share": r[42],
            "disproportionality": r[43],
            "dominant_resource": r[44],
        }
        for r in cursor.fetchall()
    ]


# --- Forensic Capture Functions ---


def create_forensic_capture(
    conn: sqlite3.Connection,
    event_id: int,
    trigger: str,
) -> int:
    """Create a forensic capture record, return capture_id."""
    cursor = conn.execute(
        """INSERT INTO forensic_captures (event_id, captured_at, trigger)
           VALUES (?, ?, ?)""",
        (event_id, time.time(), trigger),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def update_forensic_capture_status(
    conn: sqlite3.Connection,
    capture_id: int,
    spindump_status: str | None = None,
    tailspin_status: str | None = None,
    logs_status: str | None = None,
) -> None:
    """Update capture status fields."""
    conn.execute(
        """UPDATE forensic_captures
           SET spindump_status = ?, tailspin_status = ?, logs_status = ?
           WHERE id = ?""",
        (spindump_status, tailspin_status, logs_status, capture_id),
    )
    conn.commit()


def insert_spindump_process(
    conn: sqlite3.Connection,
    capture_id: int,
    pid: int,
    name: str,
    path: str | None = None,
    parent_pid: int | None = None,
    parent_name: str | None = None,
    footprint_mb: float | None = None,
    cpu_time_sec: float | None = None,
    thread_count: int | None = None,
) -> int:
    """Insert spindump process record, return process_id."""
    cursor = conn.execute(
        """INSERT INTO spindump_processes
           (capture_id, pid, name, path, parent_pid, parent_name,
            footprint_mb, cpu_time_sec, thread_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            pid,
            name,
            path,
            parent_pid,
            parent_name,
            footprint_mb,
            cpu_time_sec,
            thread_count,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_spindump_thread(
    conn: sqlite3.Connection,
    process_id: int,
    thread_id: str,
    thread_name: str | None = None,
    sample_count: int | None = None,
    priority: int | None = None,
    cpu_time_sec: float | None = None,
    state: str | None = None,
    blocked_on: str | None = None,
) -> None:
    """Insert spindump thread record."""
    conn.execute(
        """INSERT INTO spindump_threads
           (process_id, thread_id, thread_name, sample_count,
            priority, cpu_time_sec, state, blocked_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            process_id,
            thread_id,
            thread_name,
            sample_count,
            priority,
            cpu_time_sec,
            state,
            blocked_on,
        ),
    )
    conn.commit()


def insert_log_entry(
    conn: sqlite3.Connection,
    capture_id: int,
    timestamp: str,
    event_message: str,
    mach_timestamp: int | None = None,
    subsystem: str | None = None,
    category: str | None = None,
    process_name: str | None = None,
    process_id: int | None = None,
    message_type: str | None = None,
) -> None:
    """Insert log entry record."""
    conn.execute(
        """INSERT INTO log_entries
           (capture_id, timestamp, event_message, mach_timestamp,
            subsystem, category, process_name, process_id, message_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            timestamp,
            event_message,
            mach_timestamp,
            subsystem,
            category,
            process_name,
            process_id,
            message_type,
        ),
    )
    conn.commit()


def insert_buffer_context(
    conn: sqlite3.Connection,
    capture_id: int,
    sample_count: int,
    peak_score: int,
    culprits: str,
) -> None:
    """Insert buffer context record (culprits is JSON string)."""
    conn.execute(
        """INSERT INTO buffer_context
           (capture_id, sample_count, peak_score, culprits)
           VALUES (?, ?, ?, ?)""",
        (capture_id, sample_count, peak_score, culprits),
    )
    conn.commit()


def get_forensic_captures(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Get all forensic captures for an event."""
    cursor = conn.execute(
        """SELECT id, event_id, captured_at, trigger,
                  spindump_status, tailspin_status, logs_status
           FROM forensic_captures WHERE event_id = ?
           ORDER BY captured_at""",
        (event_id,),
    )
    return [
        {
            "id": r[0],
            "event_id": r[1],
            "captured_at": r[2],
            "trigger": r[3],
            "spindump_status": r[4],
            "tailspin_status": r[5],
            "logs_status": r[6],
        }
        for r in cursor.fetchall()
    ]


def get_spindump_processes(conn: sqlite3.Connection, capture_id: int) -> list[dict]:
    """Get spindump processes for a capture."""
    cursor = conn.execute(
        """SELECT id, capture_id, pid, name, path, parent_pid, parent_name,
                  footprint_mb, cpu_time_sec, thread_count
           FROM spindump_processes WHERE capture_id = ?
           ORDER BY footprint_mb DESC NULLS LAST""",
        (capture_id,),
    )
    return [
        {
            "id": r[0],
            "capture_id": r[1],
            "pid": r[2],
            "name": r[3],
            "path": r[4],
            "parent_pid": r[5],
            "parent_name": r[6],
            "footprint_mb": r[7],
            "cpu_time_sec": r[8],
            "thread_count": r[9],
        }
        for r in cursor.fetchall()
    ]


def get_spindump_threads(conn: sqlite3.Connection, process_id: int) -> list[dict]:
    """Get threads for a spindump process."""
    cursor = conn.execute(
        """SELECT id, process_id, thread_id, thread_name, sample_count,
                  priority, cpu_time_sec, state, blocked_on
           FROM spindump_threads WHERE process_id = ?
           ORDER BY sample_count DESC NULLS LAST""",
        (process_id,),
    )
    return [
        {
            "id": r[0],
            "process_id": r[1],
            "thread_id": r[2],
            "thread_name": r[3],
            "sample_count": r[4],
            "priority": r[5],
            "cpu_time_sec": r[6],
            "state": r[7],
            "blocked_on": r[8],
        }
        for r in cursor.fetchall()
    ]


def get_log_entries(
    conn: sqlite3.Connection,
    capture_id: int,
    limit: int = 100,
) -> list[dict]:
    """Get log entries for a capture."""
    cursor = conn.execute(
        """SELECT id, capture_id, timestamp, mach_timestamp, subsystem,
                  category, process_name, process_id, message_type, event_message
           FROM log_entries WHERE capture_id = ?
           ORDER BY timestamp LIMIT ?""",
        (capture_id, limit),
    )
    return [
        {
            "id": r[0],
            "capture_id": r[1],
            "timestamp": r[2],
            "mach_timestamp": r[3],
            "subsystem": r[4],
            "category": r[5],
            "process_name": r[6],
            "process_id": r[7],
            "message_type": r[8],
            "event_message": r[9],
        }
        for r in cursor.fetchall()
    ]


def get_buffer_context(conn: sqlite3.Connection, capture_id: int) -> dict | None:
    """Get buffer context for a capture."""
    row = conn.execute(
        """SELECT id, capture_id, sample_count, peak_score, culprits
           FROM buffer_context WHERE capture_id = ?""",
        (capture_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "capture_id": row[1],
        "sample_count": row[2],
        "peak_score": row[3],
        "culprits": row[4],
    }
