"""SQLite storage layer for pause-monitor."""

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from pause_monitor.stress import StressBreakdown

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
        row = conn.execute("SELECT value FROM daemon_state WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


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
