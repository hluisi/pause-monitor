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
        stress=StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
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

    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
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


def test_prune_old_data_deletes_old_samples(initialized_db: Path, sample_stress):
    """prune_old_data deletes samples older than cutoff."""
    import time

    from pause_monitor.storage import Sample, insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old sample (40 days ago)
    old_sample = Sample(
        timestamp=datetime.fromtimestamp(time.time() - 40 * 86400),
        interval=5.0,
        cpu_pct=25.0,
        load_avg=1.5,
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
    insert_sample(conn, old_sample)

    # Insert recent sample (1 day ago)
    recent_sample = Sample(
        timestamp=datetime.fromtimestamp(time.time() - 1 * 86400),
        interval=5.0,
        cpu_pct=30.0,
        load_avg=2.0,
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
    insert_sample(conn, recent_sample)

    # Prune samples older than 30 days
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=90)

    # Verify old sample was deleted, recent kept
    count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    conn.close()

    assert samples_deleted == 1
    assert events_deleted == 0
    assert count == 1


def test_prune_old_data_deletes_old_events(initialized_db: Path, sample_stress):
    """prune_old_data deletes events older than cutoff."""
    import time

    from pause_monitor.storage import Event, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old event (100 days ago)
    old_event = Event(
        timestamp=datetime.fromtimestamp(time.time() - 100 * 86400),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        notes=None,
    )
    insert_event(conn, old_event)

    # Insert recent event (30 days ago)
    recent_event = Event(
        timestamp=datetime.fromtimestamp(time.time() - 30 * 86400),
        duration=1.5,
        stress=sample_stress,
        culprits=["another_process"],
        event_dir=None,
        notes=None,
    )
    insert_event(conn, recent_event)

    # Prune events older than 90 days
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=90)

    # Verify old event was deleted, recent kept
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert samples_deleted == 0
    assert events_deleted == 1
    assert count == 1


def test_prune_old_data_deletes_process_samples(initialized_db: Path, sample_stress):
    """prune_old_data deletes process_samples linked to old samples."""
    import time

    from pause_monitor.storage import Sample, insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old sample (40 days ago)
    old_sample = Sample(
        timestamp=datetime.fromtimestamp(time.time() - 40 * 86400),
        interval=5.0,
        cpu_pct=25.0,
        load_avg=1.5,
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
    sample_id = insert_sample(conn, old_sample)

    # Insert process sample linked to the old sample
    conn.execute(
        """
        INSERT INTO process_samples (sample_id, pid, name, cpu_pct, mem_pct)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sample_id, 123, "test_process", 50.0, 10.0),
    )
    conn.commit()

    # Verify process sample exists
    proc_count_before = conn.execute("SELECT COUNT(*) FROM process_samples").fetchone()[0]
    assert proc_count_before == 1

    # Prune
    prune_old_data(conn, samples_days=30, events_days=90)

    # Verify process sample was deleted
    proc_count_after = conn.execute("SELECT COUNT(*) FROM process_samples").fetchone()[0]
    sample_count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    conn.close()

    assert proc_count_after == 0
    assert sample_count == 0


def test_prune_old_data_with_nothing_to_delete(initialized_db: Path, sample_stress):
    """prune_old_data returns zeros when nothing to delete."""
    import time

    from pause_monitor.storage import Event, Sample, insert_event, insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert recent sample (1 day ago)
    recent_sample = Sample(
        timestamp=datetime.fromtimestamp(time.time() - 1 * 86400),
        interval=5.0,
        cpu_pct=30.0,
        load_avg=2.0,
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
    insert_sample(conn, recent_sample)

    # Insert recent event (10 days ago)
    recent_event = Event(
        timestamp=datetime.fromtimestamp(time.time() - 10 * 86400),
        duration=1.5,
        stress=sample_stress,
        culprits=[],
        event_dir=None,
        notes=None,
    )
    insert_event(conn, recent_event)

    # Prune with default retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=90)
    conn.close()

    assert samples_deleted == 0
    assert events_deleted == 0


def test_prune_old_data_rejects_zero_samples_days(initialized_db: Path):
    """prune_old_data raises ValueError when samples_days < 1."""
    import pytest

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, samples_days=0, events_days=90)

    conn.close()


def test_prune_old_data_rejects_zero_events_days(initialized_db: Path):
    """prune_old_data raises ValueError when events_days < 1."""
    import pytest

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, samples_days=30, events_days=0)

    conn.close()


def test_prune_old_data_rejects_negative_days(initialized_db: Path):
    """prune_old_data raises ValueError for negative retention days."""
    import pytest

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, samples_days=-5, events_days=90)

    conn.close()


# --- Event Status Tests ---


def test_event_dataclass_has_status_field():
    """Event dataclass has status field with default value."""
    from pause_monitor.storage import Event

    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=stress,
        culprits=["test_process"],
        event_dir="/path/to/events/12345",
    )
    # Default should be "unreviewed"
    assert event.status == "unreviewed"
    assert event.notes is None


def test_event_dataclass_accepts_status():
    """Event dataclass accepts explicit status value."""
    from pause_monitor.storage import Event

    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=stress,
        culprits=["test_process"],
        event_dir="/path/to/events/12345",
        status="pinned",
        notes="Important event",
    )
    assert event.status == "pinned"
    assert event.notes == "Important event"


def test_insert_event_with_status(initialized_db: Path, sample_stress):
    """Events can be inserted with status."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="unreviewed",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "unreviewed"


def test_insert_event_with_pinned_status(initialized_db: Path, sample_stress):
    """Events can be inserted with pinned status."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="pinned",
        notes="Keep this one",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "pinned"
    assert retrieved.notes == "Keep this one"


def test_update_event_status(initialized_db: Path, sample_stress):
    """Event status can be updated."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event, update_event_status

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="unreviewed",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)

    # Update status only
    update_event_status(conn, event_id, "reviewed")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "reviewed"


def test_update_event_status_with_notes(initialized_db: Path, sample_stress):
    """Event status can be updated with notes."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event, update_event_status

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="unreviewed",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)

    # Update status with notes
    update_event_status(conn, event_id, "pinned", "Chrome memory leak")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "pinned"
    assert retrieved.notes == "Chrome memory leak"


def test_update_event_status_preserves_notes(initialized_db: Path, sample_stress):
    """Updating status without notes preserves existing notes."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event, update_event_status

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="unreviewed",
        notes="Original note",
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)

    # Update status only (notes=None should preserve existing notes)
    update_event_status(conn, event_id, "reviewed")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "reviewed"
    assert retrieved.notes == "Original note"


def test_get_events_returns_status(initialized_db: Path, sample_stress):
    """get_events includes status field in results."""
    from pause_monitor.storage import Event, get_events, insert_event

    conn = sqlite3.connect(initialized_db)

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="pinned",
        notes="Test note",
    )
    insert_event(conn, event)

    events = get_events(conn, limit=10)
    conn.close()

    assert len(events) == 1
    assert events[0].status == "pinned"
    assert events[0].notes == "Test note"


def test_migrate_add_event_status(tmp_path: Path):
    """migrate_add_event_status adds status column to existing database."""
    from pause_monitor.storage import migrate_add_event_status

    # Create a database with old schema (no status column)
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE events (
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
        )
    """)

    # Insert a legacy event
    conn.execute(
        "INSERT INTO events (timestamp, duration, stress_total) VALUES (?, ?, ?)",
        (1000000.0, 2.5, 50),
    )
    conn.commit()

    # Run migration
    migrate_add_event_status(conn)

    # Verify column exists
    cursor = conn.execute("PRAGMA table_info(events)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "status" in columns

    # Verify existing event has 'reviewed' status (not 'unreviewed')
    row = conn.execute("SELECT status FROM events WHERE id = 1").fetchone()
    assert row[0] == "reviewed"

    conn.close()


def test_migrate_add_event_status_idempotent(initialized_db: Path, sample_stress):
    """migrate_add_event_status is safe to run multiple times."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event, migrate_add_event_status

    conn = sqlite3.connect(initialized_db)

    # Insert event with status
    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="pinned",
    )
    event_id = insert_event(conn, event)

    # Run migration (should be no-op since column exists)
    migrate_add_event_status(conn)

    # Status should be preserved
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved.status == "pinned"
