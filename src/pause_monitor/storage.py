"""SQLite storage layer for pause-monitor."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

import structlog

if TYPE_CHECKING:
    from pause_monitor.collector import ProcessScore

log = structlog.get_logger()

SCHEMA_VERSION = 13  # Full MetricValue storage with low/high columns


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
    -- CPU (MetricValue: current/low/high)
    cpu REAL NOT NULL,
    cpu_low REAL NOT NULL,
    cpu_high REAL NOT NULL,
    -- Memory (MetricValue + plain mem_peak)
    mem INTEGER NOT NULL,
    mem_low INTEGER NOT NULL,
    mem_high INTEGER NOT NULL,
    mem_peak INTEGER NOT NULL,
    pageins INTEGER NOT NULL,
    pageins_low INTEGER NOT NULL,
    pageins_high INTEGER NOT NULL,
    faults INTEGER NOT NULL,
    faults_low INTEGER NOT NULL,
    faults_high INTEGER NOT NULL,
    -- Disk I/O (MetricValue)
    disk_io INTEGER NOT NULL,
    disk_io_low INTEGER NOT NULL,
    disk_io_high INTEGER NOT NULL,
    disk_io_rate REAL NOT NULL,
    disk_io_rate_low REAL NOT NULL,
    disk_io_rate_high REAL NOT NULL,
    -- Activity (MetricValue)
    csw INTEGER NOT NULL,
    csw_low INTEGER NOT NULL,
    csw_high INTEGER NOT NULL,
    syscalls INTEGER NOT NULL,
    syscalls_low INTEGER NOT NULL,
    syscalls_high INTEGER NOT NULL,
    threads INTEGER NOT NULL,
    threads_low INTEGER NOT NULL,
    threads_high INTEGER NOT NULL,
    mach_msgs INTEGER NOT NULL,
    mach_msgs_low INTEGER NOT NULL,
    mach_msgs_high INTEGER NOT NULL,
    -- Efficiency (MetricValue)
    instructions INTEGER NOT NULL,
    instructions_low INTEGER NOT NULL,
    instructions_high INTEGER NOT NULL,
    cycles INTEGER NOT NULL,
    cycles_low INTEGER NOT NULL,
    cycles_high INTEGER NOT NULL,
    ipc REAL NOT NULL,
    ipc_low REAL NOT NULL,
    ipc_high REAL NOT NULL,
    -- Power (MetricValue)
    energy INTEGER NOT NULL,
    energy_low INTEGER NOT NULL,
    energy_high INTEGER NOT NULL,
    energy_rate REAL NOT NULL,
    energy_rate_low REAL NOT NULL,
    energy_rate_high REAL NOT NULL,
    wakeups INTEGER NOT NULL,
    wakeups_low INTEGER NOT NULL,
    wakeups_high INTEGER NOT NULL,
    -- State (MetricValueStr: current/low/high)
    state TEXT NOT NULL,
    state_low TEXT NOT NULL,
    state_high TEXT NOT NULL,
    priority INTEGER NOT NULL,
    priority_low INTEGER NOT NULL,
    priority_high INTEGER NOT NULL,
    -- Scoring (MetricValue + MetricValueStr)
    score INTEGER NOT NULL,
    score_low INTEGER NOT NULL,
    score_high INTEGER NOT NULL,
    band TEXT NOT NULL,
    band_low TEXT NOT NULL,
    band_high TEXT NOT NULL,
    categories TEXT NOT NULL,
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
    row = conn.execute(
        """SELECT e.id, e.pid, e.command, e.boot_time, e.entry_time, e.exit_time,
                  e.entry_band, e.peak_band, e.peak_score, e.peak_snapshot_id,
                  s.cpu, s.cpu_low, s.cpu_high,
                  s.state, s.state_low, s.state_high,
                  s.mem, s.mem_low, s.mem_high, s.mem_peak,
                  s.pageins, s.pageins_low, s.pageins_high,
                  s.faults, s.faults_low, s.faults_high,
                  s.disk_io, s.disk_io_low, s.disk_io_high,
                  s.disk_io_rate, s.disk_io_rate_low, s.disk_io_rate_high,
                  s.csw, s.csw_low, s.csw_high,
                  s.syscalls, s.syscalls_low, s.syscalls_high,
                  s.threads, s.threads_low, s.threads_high,
                  s.mach_msgs, s.mach_msgs_low, s.mach_msgs_high,
                  s.instructions, s.instructions_low, s.instructions_high,
                  s.cycles, s.cycles_low, s.cycles_high,
                  s.ipc, s.ipc_low, s.ipc_high,
                  s.energy, s.energy_low, s.energy_high,
                  s.energy_rate, s.energy_rate_low, s.energy_rate_high,
                  s.wakeups, s.wakeups_low, s.wakeups_high,
                  s.priority, s.priority_low, s.priority_high,
                  s.score, s.score_low, s.score_high,
                  s.band, s.band_low, s.band_high,
                  s.categories, s.captured_at
           FROM process_events e
           LEFT JOIN process_snapshots s ON e.peak_snapshot_id = s.id
           WHERE e.id = ?""",
        (event_id,),
    ).fetchone()

    if not row:
        return None

    # Build peak_snapshot dict from joined columns (or None if no snapshot)
    peak_snapshot = None
    if row[9] is not None:  # peak_snapshot_id exists
        peak_snapshot = {
            "cpu": {"current": row[10], "low": row[11], "high": row[12]},
            "state": {"current": row[13], "low": row[14], "high": row[15]},
            "mem": {"current": row[16], "low": row[17], "high": row[18]},
            "mem_peak": row[19],
            "pageins": {"current": row[20], "low": row[21], "high": row[22]},
            "faults": {"current": row[23], "low": row[24], "high": row[25]},
            "disk_io": {"current": row[26], "low": row[27], "high": row[28]},
            "disk_io_rate": {"current": row[29], "low": row[30], "high": row[31]},
            "csw": {"current": row[32], "low": row[33], "high": row[34]},
            "syscalls": {"current": row[35], "low": row[36], "high": row[37]},
            "threads": {"current": row[38], "low": row[39], "high": row[40]},
            "mach_msgs": {"current": row[41], "low": row[42], "high": row[43]},
            "instructions": {"current": row[44], "low": row[45], "high": row[46]},
            "cycles": {"current": row[47], "low": row[48], "high": row[49]},
            "ipc": {"current": row[50], "low": row[51], "high": row[52]},
            "energy": {"current": row[53], "low": row[54], "high": row[55]},
            "energy_rate": {"current": row[56], "low": row[57], "high": row[58]},
            "wakeups": {"current": row[59], "low": row[60], "high": row[61]},
            "priority": {"current": row[62], "low": row[63], "high": row[64]},
            "score": {"current": row[65], "low": row[66], "high": row[67]},
            "band": {"current": row[68], "low": row[69], "high": row[70]},
            "categories": json.loads(row[71]) if row[71] else [],
            "captured_at": row[72],
        }

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
        "peak_snapshot_id": row[9],
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
    """Insert a snapshot for an event. Returns snapshot ID.

    Saves full MetricValue (current/low/high) for all metric fields.
    """
    cursor = conn.execute(
        """INSERT INTO process_snapshots
           (event_id, snapshot_type, captured_at,
            cpu, cpu_low, cpu_high,
            mem, mem_low, mem_high, mem_peak,
            pageins, pageins_low, pageins_high,
            faults, faults_low, faults_high,
            disk_io, disk_io_low, disk_io_high,
            disk_io_rate, disk_io_rate_low, disk_io_rate_high,
            csw, csw_low, csw_high,
            syscalls, syscalls_low, syscalls_high,
            threads, threads_low, threads_high,
            mach_msgs, mach_msgs_low, mach_msgs_high,
            instructions, instructions_low, instructions_high,
            cycles, cycles_low, cycles_high,
            ipc, ipc_low, ipc_high,
            energy, energy_low, energy_high,
            energy_rate, energy_rate_low, energy_rate_high,
            wakeups, wakeups_low, wakeups_high,
            state, state_low, state_high,
            priority, priority_low, priority_high,
            score, score_low, score_high,
            band, band_low, band_high,
            categories)
           VALUES (?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?)""",
        (
            event_id,
            snapshot_type,
            score.captured_at,
            # CPU
            score.cpu.current,
            score.cpu.low,
            score.cpu.high,
            # Memory
            score.mem.current,
            score.mem.low,
            score.mem.high,
            score.mem_peak,
            score.pageins.current,
            score.pageins.low,
            score.pageins.high,
            score.faults.current,
            score.faults.low,
            score.faults.high,
            # Disk I/O
            score.disk_io.current,
            score.disk_io.low,
            score.disk_io.high,
            score.disk_io_rate.current,
            score.disk_io_rate.low,
            score.disk_io_rate.high,
            # Activity
            score.csw.current,
            score.csw.low,
            score.csw.high,
            score.syscalls.current,
            score.syscalls.low,
            score.syscalls.high,
            score.threads.current,
            score.threads.low,
            score.threads.high,
            score.mach_msgs.current,
            score.mach_msgs.low,
            score.mach_msgs.high,
            # Efficiency
            score.instructions.current,
            score.instructions.low,
            score.instructions.high,
            score.cycles.current,
            score.cycles.low,
            score.cycles.high,
            score.ipc.current,
            score.ipc.low,
            score.ipc.high,
            # Power
            score.energy.current,
            score.energy.low,
            score.energy.high,
            score.energy_rate.current,
            score.energy_rate.low,
            score.energy_rate.high,
            score.wakeups.current,
            score.wakeups.low,
            score.wakeups.high,
            # State
            score.state.current,
            score.state.low,
            score.state.high,
            score.priority.current,
            score.priority.low,
            score.priority.high,
            # Scoring
            score.score.current,
            score.score.low,
            score.score.high,
            score.band.current,
            score.band.low,
            score.band.high,
            json.dumps(score.categories),
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    """Get a snapshot by ID with all fields as MetricValue-compatible dicts."""
    row = conn.execute(
        """SELECT id, event_id, snapshot_type, captured_at,
                  cpu, cpu_low, cpu_high,
                  mem, mem_low, mem_high, mem_peak,
                  pageins, pageins_low, pageins_high,
                  faults, faults_low, faults_high,
                  disk_io, disk_io_low, disk_io_high,
                  disk_io_rate, disk_io_rate_low, disk_io_rate_high,
                  csw, csw_low, csw_high,
                  syscalls, syscalls_low, syscalls_high,
                  threads, threads_low, threads_high,
                  mach_msgs, mach_msgs_low, mach_msgs_high,
                  instructions, instructions_low, instructions_high,
                  cycles, cycles_low, cycles_high,
                  ipc, ipc_low, ipc_high,
                  energy, energy_low, energy_high,
                  energy_rate, energy_rate_low, energy_rate_high,
                  wakeups, wakeups_low, wakeups_high,
                  state, state_low, state_high,
                  priority, priority_low, priority_high,
                  score, score_low, score_high,
                  band, band_low, band_high,
                  categories
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
        "cpu": {"current": row[4], "low": row[5], "high": row[6]},
        # Memory
        "mem": {"current": row[7], "low": row[8], "high": row[9]},
        "mem_peak": row[10],
        "pageins": {"current": row[11], "low": row[12], "high": row[13]},
        "faults": {"current": row[14], "low": row[15], "high": row[16]},
        # Disk I/O
        "disk_io": {"current": row[17], "low": row[18], "high": row[19]},
        "disk_io_rate": {"current": row[20], "low": row[21], "high": row[22]},
        # Activity
        "csw": {"current": row[23], "low": row[24], "high": row[25]},
        "syscalls": {"current": row[26], "low": row[27], "high": row[28]},
        "threads": {"current": row[29], "low": row[30], "high": row[31]},
        "mach_msgs": {"current": row[32], "low": row[33], "high": row[34]},
        # Efficiency
        "instructions": {"current": row[35], "low": row[36], "high": row[37]},
        "cycles": {"current": row[38], "low": row[39], "high": row[40]},
        "ipc": {"current": row[41], "low": row[42], "high": row[43]},
        # Power
        "energy": {"current": row[44], "low": row[45], "high": row[46]},
        "energy_rate": {"current": row[47], "low": row[48], "high": row[49]},
        "wakeups": {"current": row[50], "low": row[51], "high": row[52]},
        # State
        "state": {"current": row[53], "low": row[54], "high": row[55]},
        "priority": {"current": row[56], "low": row[57], "high": row[58]},
        # Scoring
        "score": {"current": row[59], "low": row[60], "high": row[61]},
        "band": {"current": row[62], "low": row[63], "high": row[64]},
        "categories": json.loads(row[65]) if row[65] else [],
    }


def get_process_snapshots(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Get all snapshots for an event, ordered by capture time.

    Returns MetricValue-compatible dicts with current/low/high.
    """
    cursor = conn.execute(
        """SELECT id, event_id, snapshot_type, captured_at,
                  cpu, cpu_low, cpu_high,
                  mem, mem_low, mem_high, mem_peak,
                  pageins, pageins_low, pageins_high,
                  faults, faults_low, faults_high,
                  disk_io, disk_io_low, disk_io_high,
                  disk_io_rate, disk_io_rate_low, disk_io_rate_high,
                  csw, csw_low, csw_high,
                  syscalls, syscalls_low, syscalls_high,
                  threads, threads_low, threads_high,
                  mach_msgs, mach_msgs_low, mach_msgs_high,
                  instructions, instructions_low, instructions_high,
                  cycles, cycles_low, cycles_high,
                  ipc, ipc_low, ipc_high,
                  energy, energy_low, energy_high,
                  energy_rate, energy_rate_low, energy_rate_high,
                  wakeups, wakeups_low, wakeups_high,
                  state, state_low, state_high,
                  priority, priority_low, priority_high,
                  score, score_low, score_high,
                  band, band_low, band_high,
                  categories
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
            "cpu": {"current": r[4], "low": r[5], "high": r[6]},
            # Memory
            "mem": {"current": r[7], "low": r[8], "high": r[9]},
            "mem_peak": r[10],
            "pageins": {"current": r[11], "low": r[12], "high": r[13]},
            "faults": {"current": r[14], "low": r[15], "high": r[16]},
            # Disk I/O
            "disk_io": {"current": r[17], "low": r[18], "high": r[19]},
            "disk_io_rate": {"current": r[20], "low": r[21], "high": r[22]},
            # Activity
            "csw": {"current": r[23], "low": r[24], "high": r[25]},
            "syscalls": {"current": r[26], "low": r[27], "high": r[28]},
            "threads": {"current": r[29], "low": r[30], "high": r[31]},
            "mach_msgs": {"current": r[32], "low": r[33], "high": r[34]},
            # Efficiency
            "instructions": {"current": r[35], "low": r[36], "high": r[37]},
            "cycles": {"current": r[38], "low": r[39], "high": r[40]},
            "ipc": {"current": r[41], "low": r[42], "high": r[43]},
            # Power
            "energy": {"current": r[44], "low": r[45], "high": r[46]},
            "energy_rate": {"current": r[47], "low": r[48], "high": r[49]},
            "wakeups": {"current": r[50], "low": r[51], "high": r[52]},
            # State
            "state": {"current": r[53], "low": r[54], "high": r[55]},
            "priority": {"current": r[56], "low": r[57], "high": r[58]},
            # Scoring
            "score": {"current": r[59], "low": r[60], "high": r[61]},
            "band": {"current": r[62], "low": r[63], "high": r[64]},
            "categories": json.loads(r[65]) if r[65] else [],
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
