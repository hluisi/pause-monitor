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

SCHEMA_VERSION = 20  # Full normalized tailspin schema with call stacks


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

-- Tailspin header: system-wide metadata from decoded tailspin
CREATE TABLE IF NOT EXISTS tailspin_header (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL UNIQUE,
    -- Timestamps
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    steps INTEGER NOT NULL,
    sampling_interval_ms INTEGER NOT NULL,
    -- System info
    os_version TEXT NOT NULL,
    architecture TEXT NOT NULL,
    report_version INTEGER,
    hardware_model TEXT,
    active_cpus INTEGER,
    memory_gb INTEGER,
    hw_page_size INTEGER,
    vm_page_size INTEGER,
    -- Boot info
    time_since_boot_sec INTEGER,
    time_awake_since_boot_sec INTEGER,
    -- CPU totals
    total_cpu_time_sec REAL,
    total_cycles INTEGER,
    total_instructions INTEGER,
    total_cpi REAL,
    -- Memory
    memory_pressure_avg_pct INTEGER,
    memory_pressure_max_pct INTEGER,
    available_memory_avg_gb REAL,
    available_memory_min_gb REAL,
    -- Disk
    free_disk_gb REAL,
    total_disk_gb REAL,
    -- Advisory
    advisory_battery INTEGER,
    advisory_user INTEGER,
    advisory_thermal INTEGER,
    advisory_combined INTEGER,
    -- Other
    shared_cache_residency_pct REAL,
    vnodes_available_pct REAL,
    data_source TEXT,
    reason TEXT,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Tailspin shared caches (multiple per capture)
CREATE TABLE IF NOT EXISTS tailspin_shared_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    uuid TEXT NOT NULL,
    base_address TEXT NOT NULL,
    slide TEXT NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Tailspin I/O statistics from header
CREATE TABLE IF NOT EXISTS tailspin_io_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    tier TEXT NOT NULL,  -- 'overall', 'tier0', 'tier1', 'tier2'
    io_count INTEGER NOT NULL,
    io_rate REAL,
    bytes INTEGER NOT NULL,
    bytes_rate REAL,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Tailspin process: full process info from decoded tailspin
CREATE TABLE IF NOT EXISTS tailspin_process (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    -- Optional identifiers
    uuid TEXT,
    path TEXT,
    identifier TEXT,  -- bundle ID
    version TEXT,
    -- Relationships
    parent_pid INTEGER,
    parent_name TEXT,
    responsible_pid INTEGER,
    responsible_name TEXT,
    execed_from_pid INTEGER,
    execed_from_name TEXT,
    execed_to_pid INTEGER,
    execed_to_name TEXT,
    -- Metadata
    architecture TEXT,
    shared_cache_uuid TEXT,
    runningboard_managed INTEGER,  -- boolean
    sudden_term TEXT,
    -- Resources
    footprint_mb REAL,
    footprint_delta_mb REAL,
    io_count INTEGER,
    io_bytes INTEGER,
    time_since_fork_sec INTEGER,
    -- Timing (for short-lived processes)
    start_time TEXT,
    end_time TEXT,
    -- Sampling
    num_samples INTEGER,
    sample_range_start INTEGER,
    sample_range_end INTEGER,
    -- CPU
    cpu_time_sec REAL,
    cycles INTEGER,
    instructions INTEGER,
    cpi REAL,
    -- Threads
    num_threads INTEGER,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Tailspin process notes (multiple per process)
CREATE TABLE IF NOT EXISTS tailspin_process_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    FOREIGN KEY (process_id) REFERENCES tailspin_process(id) ON DELETE CASCADE
);

-- Tailspin thread: full thread info
CREATE TABLE IF NOT EXISTS tailspin_thread (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,  -- hex like '0x3fd504'
    -- Names
    dispatch_queue_name TEXT,
    dispatch_queue_serial INTEGER,
    thread_name TEXT,
    -- Sampling
    num_samples INTEGER,
    sample_range_start INTEGER,
    sample_range_end INTEGER,
    -- Priority
    priority INTEGER,
    base_priority INTEGER,
    -- CPU
    cpu_time_sec REAL,
    cycles INTEGER,
    instructions INTEGER,
    cpi REAL,
    -- I/O
    io_count INTEGER,
    io_bytes INTEGER,
    FOREIGN KEY (process_id) REFERENCES tailspin_process(id) ON DELETE CASCADE
);

-- Tailspin stack frame: call stack with tree structure
CREATE TABLE IF NOT EXISTS tailspin_frame (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    parent_frame_id INTEGER,  -- NULL for root frames, self-referential for tree
    depth INTEGER NOT NULL,  -- 0 = root, increases with call depth
    -- Sampling
    sample_count INTEGER NOT NULL,
    -- Symbol info
    is_kernel INTEGER NOT NULL,  -- boolean, true if frame has * prefix
    symbol_name TEXT,  -- function name or NULL if unknown (???)
    symbol_offset INTEGER,  -- offset within function
    library_name TEXT,  -- library or binary name
    library_offset INTEGER,  -- offset within library
    address TEXT NOT NULL,  -- hex address like '0x19e30ab84'
    -- State (for leaf frames)
    state TEXT,  -- 'running', 'blocked', etc.
    core_type TEXT,  -- 'p-core', 'e-core', or NULL
    blocked_on TEXT,  -- e.g., 'wait4 on zsh [46454]'
    FOREIGN KEY (thread_id) REFERENCES tailspin_thread(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_frame_id) REFERENCES tailspin_frame(id) ON DELETE CASCADE
);

-- Tailspin binary images per process
CREATE TABLE IF NOT EXISTS tailspin_binary_image (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL,
    start_address TEXT NOT NULL,
    end_address TEXT,
    name TEXT NOT NULL,
    version TEXT,
    uuid TEXT,
    path TEXT,
    is_kernel INTEGER NOT NULL,  -- boolean
    FOREIGN KEY (process_id) REFERENCES tailspin_process(id) ON DELETE CASCADE
);

-- Tailspin I/O histogram buckets
CREATE TABLE IF NOT EXISTS tailspin_io_histogram (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    histogram_type TEXT NOT NULL,  -- 'io_size', 'tier0_latency', 'tier1_latency', 'tier2_latency'
    begin_value INTEGER NOT NULL,  -- in KB for size, us for latency
    end_value INTEGER,  -- NULL for overflow bucket (> X)
    frequency INTEGER NOT NULL,
    cdf INTEGER NOT NULL,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
);

-- Tailspin I/O aggregate stats
CREATE TABLE IF NOT EXISTS tailspin_io_aggregate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    tier TEXT NOT NULL,  -- 'tier0', 'tier1', 'tier2'
    num_ios INTEGER NOT NULL,
    latency_mean_us INTEGER,
    latency_max_us INTEGER,
    latency_sd_us INTEGER,
    read_count INTEGER,
    read_bytes INTEGER,
    write_count INTEGER,
    write_bytes INTEGER,
    FOREIGN KEY (capture_id) REFERENCES forensic_captures(id) ON DELETE CASCADE
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
CREATE INDEX IF NOT EXISTS idx_log_entries_capture ON log_entries(capture_id);
CREATE INDEX IF NOT EXISTS idx_buffer_context_capture ON buffer_context(capture_id);

-- Indexes for tailspin tables
CREATE INDEX IF NOT EXISTS idx_tailspin_header_capture ON tailspin_header(capture_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_shared_cache_capture ON tailspin_shared_cache(capture_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_io_stats_capture ON tailspin_io_stats(capture_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_process_capture ON tailspin_process(capture_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_process_pid ON tailspin_process(pid);
CREATE INDEX IF NOT EXISTS idx_tailspin_process_note_process ON tailspin_process_note(process_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_thread_process ON tailspin_thread(process_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_frame_thread ON tailspin_frame(thread_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_frame_parent ON tailspin_frame(parent_frame_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_binary_image_process ON tailspin_binary_image(process_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_io_histogram_capture ON tailspin_io_histogram(capture_id);
CREATE INDEX IF NOT EXISTS idx_tailspin_io_aggregate_capture ON tailspin_io_aggregate(capture_id);

-- Machine snapshots: periodic full-system state (every 60s, retained 12h)
CREATE TABLE IF NOT EXISTS machine_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,
    process_count INTEGER NOT NULL,
    max_score INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS machine_snapshot_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    command TEXT NOT NULL,
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
    -- Scoring
    score INTEGER NOT NULL,
    band TEXT NOT NULL,
    cpu_share REAL NOT NULL,
    gpu_share REAL NOT NULL,
    mem_share REAL NOT NULL,
    disk_share REAL NOT NULL,
    wakeups_share REAL NOT NULL,
    disproportionality REAL NOT NULL,
    dominant_resource TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES machine_snapshots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_machine_snapshots_time ON machine_snapshots(captured_at);
CREATE INDEX IF NOT EXISTS idx_msp_snapshot ON machine_snapshot_processes(snapshot_id);
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
    """Get current schema version from database.

    Requires database to be initialized first (daemon_state table must exist).
    """
    row = conn.execute("SELECT value FROM daemon_state WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0


def get_daemon_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a value from daemon_state table.

    Requires database to be initialized first (daemon_state table must exist).
    """
    row = conn.execute("SELECT value FROM daemon_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


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


def close_stale_open_events(conn: sqlite3.Connection, exit_time: float) -> int:
    """Close all open events (daemon restart cleanup).

    Called on daemon startup to close any events that were left open
    from a previous daemon run (crash, restart, etc.).

    Args:
        conn: Database connection
        exit_time: Timestamp to use as exit_time for closed events

    Returns:
        Number of events closed
    """
    cursor = conn.execute(
        "UPDATE process_events SET exit_time = ? WHERE exit_time IS NULL",
        (exit_time,),
    )
    count = cursor.rowcount
    if count > 0:
        conn.commit()
        log.info("stale_events_closed", count=count)
    return count


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


def insert_tailspin_header(
    conn: sqlite3.Connection,
    capture_id: int,
    *,
    start_time: str,
    end_time: str,
    duration_sec: float,
    steps: int,
    sampling_interval_ms: int,
    os_version: str,
    architecture: str,
    report_version: int | None = None,
    hardware_model: str | None = None,
    active_cpus: int | None = None,
    memory_gb: int | None = None,
    hw_page_size: int | None = None,
    vm_page_size: int | None = None,
    time_since_boot_sec: int | None = None,
    time_awake_since_boot_sec: int | None = None,
    total_cpu_time_sec: float | None = None,
    total_cycles: int | None = None,
    total_instructions: int | None = None,
    total_cpi: float | None = None,
    memory_pressure_avg_pct: int | None = None,
    memory_pressure_max_pct: int | None = None,
    available_memory_avg_gb: float | None = None,
    available_memory_min_gb: float | None = None,
    free_disk_gb: float | None = None,
    total_disk_gb: float | None = None,
    advisory_battery: int | None = None,
    advisory_user: int | None = None,
    advisory_thermal: int | None = None,
    advisory_combined: int | None = None,
    shared_cache_residency_pct: float | None = None,
    vnodes_available_pct: float | None = None,
    data_source: str | None = None,
    reason: str | None = None,
) -> int:
    """Insert tailspin header record, return header_id."""
    cursor = conn.execute(
        """INSERT INTO tailspin_header
           (capture_id, start_time, end_time, duration_sec, steps, sampling_interval_ms,
            os_version, architecture, report_version, hardware_model, active_cpus,
            memory_gb, hw_page_size, vm_page_size, time_since_boot_sec,
            time_awake_since_boot_sec, total_cpu_time_sec, total_cycles,
            total_instructions, total_cpi, memory_pressure_avg_pct,
            memory_pressure_max_pct, available_memory_avg_gb, available_memory_min_gb,
            free_disk_gb, total_disk_gb, advisory_battery, advisory_user,
            advisory_thermal, advisory_combined, shared_cache_residency_pct,
            vnodes_available_pct, data_source, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            start_time,
            end_time,
            duration_sec,
            steps,
            sampling_interval_ms,
            os_version,
            architecture,
            report_version,
            hardware_model,
            active_cpus,
            memory_gb,
            hw_page_size,
            vm_page_size,
            time_since_boot_sec,
            time_awake_since_boot_sec,
            total_cpu_time_sec,
            total_cycles,
            total_instructions,
            total_cpi,
            memory_pressure_avg_pct,
            memory_pressure_max_pct,
            available_memory_avg_gb,
            available_memory_min_gb,
            free_disk_gb,
            total_disk_gb,
            advisory_battery,
            advisory_user,
            advisory_thermal,
            advisory_combined,
            shared_cache_residency_pct,
            vnodes_available_pct,
            data_source,
            reason,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_shared_cache(
    conn: sqlite3.Connection,
    capture_id: int,
    uuid: str,
    base_address: str,
    slide: str,
    name: str,
) -> int:
    """Insert tailspin shared cache record."""
    cursor = conn.execute(
        """INSERT INTO tailspin_shared_cache
           (capture_id, uuid, base_address, slide, name)
           VALUES (?, ?, ?, ?, ?)""",
        (capture_id, uuid, base_address, slide, name),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_io_stats(
    conn: sqlite3.Connection,
    capture_id: int,
    tier: str,
    io_count: int,
    bytes_total: int,
    io_rate: float | None = None,
    bytes_rate: float | None = None,
) -> int:
    """Insert tailspin I/O stats record."""
    cursor = conn.execute(
        """INSERT INTO tailspin_io_stats
           (capture_id, tier, io_count, io_rate, bytes, bytes_rate)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (capture_id, tier, io_count, io_rate, bytes_total, bytes_rate),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_process(
    conn: sqlite3.Connection,
    capture_id: int,
    pid: int,
    name: str,
    *,
    uuid: str | None = None,
    path: str | None = None,
    identifier: str | None = None,
    version: str | None = None,
    parent_pid: int | None = None,
    parent_name: str | None = None,
    responsible_pid: int | None = None,
    responsible_name: str | None = None,
    execed_from_pid: int | None = None,
    execed_from_name: str | None = None,
    execed_to_pid: int | None = None,
    execed_to_name: str | None = None,
    architecture: str | None = None,
    shared_cache_uuid: str | None = None,
    runningboard_managed: bool | None = None,
    sudden_term: str | None = None,
    footprint_mb: float | None = None,
    footprint_delta_mb: float | None = None,
    io_count: int | None = None,
    io_bytes: int | None = None,
    time_since_fork_sec: int | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    num_samples: int | None = None,
    sample_range_start: int | None = None,
    sample_range_end: int | None = None,
    cpu_time_sec: float | None = None,
    cycles: int | None = None,
    instructions: int | None = None,
    cpi: float | None = None,
    num_threads: int | None = None,
) -> int:
    """Insert tailspin process record, return process_id."""
    cursor = conn.execute(
        """INSERT INTO tailspin_process
           (capture_id, pid, name, uuid, path, identifier, version,
            parent_pid, parent_name, responsible_pid, responsible_name,
            execed_from_pid, execed_from_name, execed_to_pid, execed_to_name,
            architecture, shared_cache_uuid, runningboard_managed, sudden_term,
            footprint_mb, footprint_delta_mb, io_count, io_bytes, time_since_fork_sec,
            start_time, end_time, num_samples, sample_range_start, sample_range_end,
            cpu_time_sec, cycles, instructions, cpi, num_threads)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            pid,
            name,
            uuid,
            path,
            identifier,
            version,
            parent_pid,
            parent_name,
            responsible_pid,
            responsible_name,
            execed_from_pid,
            execed_from_name,
            execed_to_pid,
            execed_to_name,
            architecture,
            shared_cache_uuid,
            1 if runningboard_managed else (0 if runningboard_managed is False else None),
            sudden_term,
            footprint_mb,
            footprint_delta_mb,
            io_count,
            io_bytes,
            time_since_fork_sec,
            start_time,
            end_time,
            num_samples,
            sample_range_start,
            sample_range_end,
            cpu_time_sec,
            cycles,
            instructions,
            cpi,
            num_threads,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_process_note(
    conn: sqlite3.Connection,
    process_id: int,
    note: str,
) -> int:
    """Insert tailspin process note."""
    cursor = conn.execute(
        """INSERT INTO tailspin_process_note (process_id, note) VALUES (?, ?)""",
        (process_id, note),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_thread(
    conn: sqlite3.Connection,
    process_id: int,
    thread_id: str,
    *,
    dispatch_queue_name: str | None = None,
    dispatch_queue_serial: int | None = None,
    thread_name: str | None = None,
    num_samples: int | None = None,
    sample_range_start: int | None = None,
    sample_range_end: int | None = None,
    priority: int | None = None,
    base_priority: int | None = None,
    cpu_time_sec: float | None = None,
    cycles: int | None = None,
    instructions: int | None = None,
    cpi: float | None = None,
    io_count: int | None = None,
    io_bytes: int | None = None,
) -> int:
    """Insert tailspin thread record, return thread_id."""
    cursor = conn.execute(
        """INSERT INTO tailspin_thread
           (process_id, thread_id, dispatch_queue_name, dispatch_queue_serial,
            thread_name, num_samples, sample_range_start, sample_range_end,
            priority, base_priority, cpu_time_sec, cycles, instructions, cpi,
            io_count, io_bytes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            process_id,
            thread_id,
            dispatch_queue_name,
            dispatch_queue_serial,
            thread_name,
            num_samples,
            sample_range_start,
            sample_range_end,
            priority,
            base_priority,
            cpu_time_sec,
            cycles,
            instructions,
            cpi,
            io_count,
            io_bytes,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_frame(
    conn: sqlite3.Connection,
    thread_id: int,
    depth: int,
    sample_count: int,
    is_kernel: bool,
    address: str,
    *,
    parent_frame_id: int | None = None,
    symbol_name: str | None = None,
    symbol_offset: int | None = None,
    library_name: str | None = None,
    library_offset: int | None = None,
    state: str | None = None,
    core_type: str | None = None,
    blocked_on: str | None = None,
) -> int:
    """Insert tailspin stack frame record, return frame_id."""
    cursor = conn.execute(
        """INSERT INTO tailspin_frame
           (thread_id, parent_frame_id, depth, sample_count, is_kernel,
            symbol_name, symbol_offset, library_name, library_offset,
            address, state, core_type, blocked_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            thread_id,
            parent_frame_id,
            depth,
            sample_count,
            1 if is_kernel else 0,
            symbol_name,
            symbol_offset,
            library_name,
            library_offset,
            address,
            state,
            core_type,
            blocked_on,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_binary_image(
    conn: sqlite3.Connection,
    process_id: int,
    start_address: str,
    name: str,
    is_kernel: bool,
    *,
    end_address: str | None = None,
    version: str | None = None,
    uuid: str | None = None,
    path: str | None = None,
) -> int:
    """Insert tailspin binary image record."""
    cursor = conn.execute(
        """INSERT INTO tailspin_binary_image
           (process_id, start_address, end_address, name, version, uuid, path, is_kernel)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (process_id, start_address, end_address, name, version, uuid, path, 1 if is_kernel else 0),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_io_histogram(
    conn: sqlite3.Connection,
    capture_id: int,
    histogram_type: str,
    begin_value: int,
    frequency: int,
    cdf: int,
    end_value: int | None = None,
) -> int:
    """Insert tailspin I/O histogram bucket."""
    cursor = conn.execute(
        """INSERT INTO tailspin_io_histogram
           (capture_id, histogram_type, begin_value, end_value, frequency, cdf)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (capture_id, histogram_type, begin_value, end_value, frequency, cdf),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


def insert_tailspin_io_aggregate(
    conn: sqlite3.Connection,
    capture_id: int,
    tier: str,
    num_ios: int,
    *,
    latency_mean_us: int | None = None,
    latency_max_us: int | None = None,
    latency_sd_us: int | None = None,
    read_count: int | None = None,
    read_bytes: int | None = None,
    write_count: int | None = None,
    write_bytes: int | None = None,
) -> int:
    """Insert tailspin I/O aggregate stats."""
    cursor = conn.execute(
        """INSERT INTO tailspin_io_aggregate
           (capture_id, tier, num_ios, latency_mean_us, latency_max_us,
            latency_sd_us, read_count, read_bytes, write_count, write_bytes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            tier,
            num_ios,
            latency_mean_us,
            latency_max_us,
            latency_sd_us,
            read_count,
            read_bytes,
            write_count,
            write_bytes,
        ),
    )
    conn.commit()
    result = cursor.lastrowid
    assert result is not None
    return result


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


def get_tailspin_header(conn: sqlite3.Connection, capture_id: int) -> dict | None:
    """Get tailspin header for a capture."""
    row = conn.execute(
        """SELECT * FROM tailspin_header WHERE capture_id = ?""",
        (capture_id,),
    ).fetchone()
    if not row:
        return None
    columns = [d[0] for d in conn.execute("SELECT * FROM tailspin_header LIMIT 0").description]
    return dict(zip(columns, row))


def get_tailspin_processes(conn: sqlite3.Connection, capture_id: int) -> list[dict]:
    """Get tailspin processes for a capture."""
    cursor = conn.execute(
        """SELECT * FROM tailspin_process WHERE capture_id = ?
           ORDER BY cpu_time_sec DESC NULLS LAST""",
        (capture_id,),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def get_tailspin_threads(conn: sqlite3.Connection, process_id: int) -> list[dict]:
    """Get threads for a tailspin process."""
    cursor = conn.execute(
        """SELECT * FROM tailspin_thread WHERE process_id = ?
           ORDER BY num_samples DESC NULLS LAST""",
        (process_id,),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def get_tailspin_frames(conn: sqlite3.Connection, thread_id: int) -> list[dict]:
    """Get stack frames for a tailspin thread, ordered by depth."""
    cursor = conn.execute(
        """SELECT * FROM tailspin_frame WHERE thread_id = ?
           ORDER BY depth, id""",
        (thread_id,),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def get_tailspin_binary_images(conn: sqlite3.Connection, process_id: int) -> list[dict]:
    """Get binary images for a tailspin process."""
    cursor = conn.execute(
        """SELECT * FROM tailspin_binary_image WHERE process_id = ?
           ORDER BY start_address""",
        (process_id,),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


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


# ─────────────────────────────────────────────────────────────────────────────
# Machine Snapshots (periodic full-system state)
# ─────────────────────────────────────────────────────────────────────────────


def insert_machine_snapshot(
    conn: sqlite3.Connection,
    captured_at: float,
    processes: list["ProcessScore"],
) -> int:
    """Insert a full machine snapshot with all process data.

    Args:
        conn: Database connection
        captured_at: Timestamp of the snapshot
        processes: All scored processes at this moment

    Returns:
        The snapshot ID
    """
    max_score = max((p.score for p in processes), default=0)

    # Insert snapshot header
    cursor = conn.execute(
        """INSERT INTO machine_snapshots (captured_at, process_count, max_score)
           VALUES (?, ?, ?)""",
        (captured_at, len(processes), max_score),
    )
    snapshot_id = cursor.lastrowid
    assert snapshot_id is not None

    # Insert all processes
    conn.executemany(
        """INSERT INTO machine_snapshot_processes
           (snapshot_id, pid, command,
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
        [
            (
                snapshot_id,
                p.pid,
                p.command,
                # CPU
                p.cpu,
                # Memory
                p.mem,
                p.mem_peak,
                p.pageins,
                p.pageins_rate,
                p.faults,
                p.faults_rate,
                # Disk I/O
                p.disk_io,
                p.disk_io_rate,
                # Activity
                p.csw,
                p.csw_rate,
                p.syscalls,
                p.syscalls_rate,
                p.threads,
                p.mach_msgs,
                p.mach_msgs_rate,
                # Efficiency
                p.instructions,
                p.cycles,
                p.ipc,
                # Power
                p.energy,
                p.energy_rate,
                p.wakeups,
                p.wakeups_rate,
                # Contention
                p.runnable_time,
                p.runnable_time_rate,
                p.qos_interactive,
                p.qos_interactive_rate,
                # GPU
                p.gpu_time,
                p.gpu_time_rate,
                # Zombie children
                p.zombie_children,
                # State
                p.state,
                p.priority,
                # Scoring
                p.score,
                p.band,
                p.cpu_share,
                p.gpu_share,
                p.mem_share,
                p.disk_share,
                p.wakeups_share,
                p.disproportionality,
                p.dominant_resource,
            )
            for p in processes
        ],
    )

    conn.commit()
    log.debug(
        "machine_snapshot_inserted",
        snapshot_id=snapshot_id,
        process_count=len(processes),
        max_score=max_score,
    )
    return snapshot_id


def prune_machine_snapshots(conn: sqlite3.Connection, max_age_hours: float = 12.0) -> int:
    """Delete machine snapshots older than max_age_hours.

    Args:
        conn: Database connection
        max_age_hours: Maximum age in hours (default 12)

    Returns:
        Number of snapshots deleted
    """
    cutoff = time.time() - (max_age_hours * 3600)

    # Get count before deletion
    count = conn.execute(
        "SELECT COUNT(*) FROM machine_snapshots WHERE captured_at < ?",
        (cutoff,),
    ).fetchone()[0]

    if count > 0:
        # CASCADE will delete associated processes
        conn.execute(
            "DELETE FROM machine_snapshots WHERE captured_at < ?",
            (cutoff,),
        )
        conn.commit()
        log.info("machine_snapshots_pruned", count=count, max_age_hours=max_age_hours)

    return count


def get_machine_snapshot_count(conn: sqlite3.Connection) -> int:
    """Get the number of machine snapshots in the database."""
    return conn.execute("SELECT COUNT(*) FROM machine_snapshots").fetchone()[0]
