"""Tests for SQLite storage layer."""

import sqlite3
from datetime import datetime
from pathlib import Path

from pause_monitor.storage import SCHEMA_VERSION, get_schema_version, init_database
from pause_monitor.stress import StressBreakdown


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
    from pause_monitor.storage import Sample, get_recent_samples, insert_sample

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


def test_event_dataclass():
    """Event has correct fields."""
    from pause_monitor.storage import Event

    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=stress,
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
    from pause_monitor.storage import Event, get_events, insert_event

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


def test_get_event_by_id_found(initialized_db: Path, sample_stress):
    """get_event_by_id returns event when found."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir="/path/to/event",
        notes="Test note",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.id == event_id
    assert retrieved.duration == 2.5
    assert retrieved.culprits == ["test_process"]


def test_get_event_by_id_not_found(initialized_db: Path):
    """get_event_by_id returns None when not found."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    result = get_event_by_id(conn, 99999)
    conn.close()

    assert result is None
