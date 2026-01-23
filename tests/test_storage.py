"""Tests for SQLite storage layer."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.storage import SCHEMA_VERSION, Sample, get_schema_version, init_database
from pause_monitor.stress import StressBreakdown


def make_test_sample(**kwargs) -> Sample:
    """Create Sample with sensible defaults for testing."""
    defaults = {
        "timestamp": datetime.now(),
        "interval": 0.1,
        "load_avg": 1.0,
        "mem_pressure": 50,
        "throttled": False,
        "cpu_power": 5.0,
        "gpu_pct": 10.0,
        "gpu_power": 1.0,
        "io_read_per_s": 1000.0,
        "io_write_per_s": 500.0,
        "wakeups_per_s": 50.0,
        "pageins_per_s": 0.0,
        "stress": StressBreakdown(
            load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0, pageins=0
        ),
    }
    defaults.update(kwargs)
    return Sample(**defaults)


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
    """Sample has correct fields matching Data Dictionary."""
    sample = make_test_sample(
        interval=5.0,
        load_avg=1.5,
        mem_pressure=45,
        throttled=False,
        cpu_power=5.2,
        gpu_pct=10.0,
        gpu_power=1.5,
        io_read_per_s=1024.0,
        io_write_per_s=512.0,
        wakeups_per_s=150.0,
        pageins_per_s=0.0,
        stress=StressBreakdown(
            load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0, pageins=0
        ),
    )
    assert sample.io_read_per_s == 1024.0
    assert sample.wakeups_per_s == 150.0
    assert sample.pageins_per_s == 0.0
    assert sample.mem_pressure == 45
    assert sample.stress.total == 15

    # Removed fields don't exist
    assert not hasattr(sample, "io_read")
    assert not hasattr(sample, "io_write")
    assert not hasattr(sample, "net_sent")
    assert not hasattr(sample, "net_recv")
    assert not hasattr(sample, "cpu_pct")
    assert not hasattr(sample, "cpu_freq")
    assert not hasattr(sample, "cpu_temp")
    assert not hasattr(sample, "mem_available")
    assert not hasattr(sample, "swap_used")


def test_insert_sample(initialized_db: Path, sample_stress):
    """insert_sample stores sample in database."""
    from pause_monitor.storage import insert_sample

    sample = make_test_sample(
        interval=5.0,
        load_avg=1.5,
        mem_pressure=50,
        throttled=False,
        cpu_power=5.0,
        gpu_pct=10.0,
        gpu_power=1.0,
        io_read_per_s=1000.0,
        io_write_per_s=500.0,
        wakeups_per_s=50.0,
        pageins_per_s=0.0,
        stress=sample_stress,
    )

    conn = sqlite3.connect(initialized_db)
    sample_id = insert_sample(conn, sample)
    conn.close()

    assert sample_id > 0


def test_get_recent_samples(initialized_db: Path, sample_stress):
    """get_recent_samples returns samples in reverse chronological order."""
    from pause_monitor.storage import get_recent_samples, insert_sample

    conn = sqlite3.connect(initialized_db)

    for i in range(5):
        sample = make_test_sample(
            timestamp=datetime.fromtimestamp(1000000 + i * 5),
            interval=5.0,
            load_avg=1.0 + i * 0.1,  # Varying load for verification
            mem_pressure=50,
            stress=sample_stress,
        )
        insert_sample(conn, sample)

    samples = get_recent_samples(conn, limit=3)
    conn.close()

    assert len(samples) == 3
    # Most recent has highest load_avg (1.4)
    assert samples[0].load_avg == pytest.approx(1.4, rel=0.01)
    assert samples[2].load_avg == pytest.approx(1.2, rel=0.01)


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

    from pause_monitor.storage import insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old sample (40 days ago)
    old_sample = make_test_sample(
        timestamp=datetime.fromtimestamp(time.time() - 40 * 86400),
        interval=5.0,
        load_avg=1.5,
        stress=sample_stress,
    )
    insert_sample(conn, old_sample)

    # Insert recent sample (1 day ago)
    recent_sample = make_test_sample(
        timestamp=datetime.fromtimestamp(time.time() - 1 * 86400),
        interval=5.0,
        load_avg=2.0,
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
    """prune_old_data deletes old events with prunable status (reviewed/dismissed)."""
    import time

    from pause_monitor.storage import Event, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old event (100 days ago) with 'reviewed' status (prunable)
    old_event = Event(
        timestamp=datetime.fromtimestamp(time.time() - 100 * 86400),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="reviewed",  # Must be reviewed/dismissed to be prunable
        notes=None,
    )
    insert_event(conn, old_event)

    # Insert recent event (30 days ago) - also reviewed but within retention
    recent_event = Event(
        timestamp=datetime.fromtimestamp(time.time() - 30 * 86400),
        duration=1.5,
        stress=sample_stress,
        culprits=["another_process"],
        event_dir=None,
        status="reviewed",
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

    from pause_monitor.storage import insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old sample (40 days ago)
    old_sample = make_test_sample(
        timestamp=datetime.fromtimestamp(time.time() - 40 * 86400),
        interval=5.0,
        load_avg=1.5,
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

    from pause_monitor.storage import Event, insert_event, insert_sample, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert recent sample (1 day ago)
    recent_sample = make_test_sample(
        timestamp=datetime.fromtimestamp(time.time() - 1 * 86400),
        interval=5.0,
        load_avg=2.0,
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


def test_update_event_status_invalid_status(initialized_db: Path, sample_stress):
    """update_event_status rejects invalid status values."""
    from pause_monitor.storage import Event, insert_event, update_event_status

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

    # Invalid status should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        update_event_status(conn, event_id, "invalid_status")

    assert "Invalid status 'invalid_status'" in str(exc_info.value)
    assert "dismissed" in str(exc_info.value)  # Shows valid options
    conn.close()


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
    """migrate_add_event_status adds status and notes columns to existing database."""
    from pause_monitor.storage import migrate_add_event_status

    # Create a database with old schema (no status or notes columns)
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
            event_dir       TEXT
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

    # Verify both columns exist
    cursor = conn.execute("PRAGMA table_info(events)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "status" in columns
    assert "notes" in columns

    # Verify existing event has 'reviewed' status (not 'unreviewed')
    row = conn.execute("SELECT status, notes FROM events WHERE id = 1").fetchone()
    assert row[0] == "reviewed"
    assert row[1] is None  # Notes should be NULL

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


# --- Status-Aware Pruning Tests ---


def test_prune_respects_unreviewed_status(initialized_db: Path, sample_stress):
    """Unreviewed events are never pruned, regardless of age."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old unreviewed event (60 days ago)
    old_time = datetime.fromtimestamp(time.time() - 60 * 86400)
    event = Event(
        timestamp=old_time,
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        status="unreviewed",
    )
    event_id = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Unreviewed event should NOT be pruned
    assert events_deleted == 0
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "unreviewed"


def test_prune_respects_pinned_status(initialized_db: Path, sample_stress):
    """Pinned events are never pruned, regardless of age."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old pinned event (60 days ago)
    old_time = datetime.fromtimestamp(time.time() - 60 * 86400)
    event = Event(
        timestamp=old_time,
        duration=2.5,
        stress=sample_stress,
        culprits=["important_process"],
        event_dir=None,
        status="pinned",
        notes="Keep this forever",
    )
    event_id = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Pinned event should NOT be pruned
    assert events_deleted == 0
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "pinned"


def test_prune_removes_dismissed_events(initialized_db: Path, sample_stress):
    """Dismissed events are pruned after retention period."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old dismissed event (60 days ago)
    old_time = datetime.fromtimestamp(time.time() - 60 * 86400)
    event = Event(
        timestamp=old_time,
        duration=2.5,
        stress=sample_stress,
        culprits=["dismissed_process"],
        event_dir=None,
        status="dismissed",
    )
    event_id = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Dismissed event SHOULD be pruned
    assert events_deleted == 1
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is None


def test_prune_removes_reviewed_events(initialized_db: Path, sample_stress):
    """Reviewed events are pruned after retention period."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old reviewed event (60 days ago)
    old_time = datetime.fromtimestamp(time.time() - 60 * 86400)
    event = Event(
        timestamp=old_time,
        duration=2.5,
        stress=sample_stress,
        culprits=["reviewed_process"],
        event_dir=None,
        status="reviewed",
    )
    event_id = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Reviewed event SHOULD be pruned
    assert events_deleted == 1
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is None


def test_prune_mixed_status_events(initialized_db: Path, sample_stress):
    """Pruning only removes reviewed/dismissed events, keeps unreviewed/pinned."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    old_time = datetime.fromtimestamp(time.time() - 60 * 86400)

    # Insert one event of each status (all old)
    events = {}
    for status in ["unreviewed", "reviewed", "pinned", "dismissed"]:
        event = Event(
            timestamp=old_time,
            duration=2.5,
            stress=sample_stress,
            culprits=[f"{status}_process"],
            event_dir=None,
            status=status,
        )
        events[status] = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Only reviewed and dismissed should be pruned (2 events)
    assert events_deleted == 2

    # Unreviewed and pinned should still exist
    assert get_event_by_id(conn, events["unreviewed"]) is not None
    assert get_event_by_id(conn, events["pinned"]) is not None

    # Reviewed and dismissed should be gone
    assert get_event_by_id(conn, events["reviewed"]) is None
    assert get_event_by_id(conn, events["dismissed"]) is None

    conn.close()


def test_prune_recent_dismissed_not_pruned(initialized_db: Path, sample_stress):
    """Recent dismissed events are NOT pruned even though they are prunable status."""
    import time

    from pause_monitor.storage import Event, get_event_by_id, insert_event, prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert recent dismissed event (10 days ago - within 30 day retention)
    recent_time = datetime.fromtimestamp(time.time() - 10 * 86400)
    event = Event(
        timestamp=recent_time,
        duration=2.5,
        stress=sample_stress,
        culprits=["dismissed_process"],
        event_dir=None,
        status="dismissed",
    )
    event_id = insert_event(conn, event)

    # Prune with 30 day retention
    samples_deleted, events_deleted = prune_old_data(conn, samples_days=30, events_days=30)

    # Recent dismissed event should NOT be pruned (within retention)
    assert events_deleted == 0
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "dismissed"


def test_insert_sample_with_gpu_and_wakeups(initialized_db: Path):
    """Samples store and retrieve GPU and wakeups stress correctly."""
    from pause_monitor.storage import get_recent_samples, insert_sample

    stress = StressBreakdown(
        load=10, memory=5, thermal=0, latency=0, io=0, gpu=15, wakeups=12, pageins=0
    )
    sample = make_test_sample(
        interval=1.0,
        load_avg=2.0,
        stress=stress,
    )

    conn = sqlite3.connect(initialized_db)
    insert_sample(conn, sample)
    samples = get_recent_samples(conn, limit=1)
    conn.close()

    assert samples[0].stress.gpu == 15
    assert samples[0].stress.wakeups == 12
    # Verify total includes gpu, wakeups, and pageins
    assert samples[0].stress.total == 10 + 5 + 0 + 0 + 0 + 15 + 12 + 0


def test_insert_event_with_gpu_and_wakeups(initialized_db: Path):
    """Events store and retrieve GPU and wakeups stress correctly."""
    from pause_monitor.storage import Event, get_event_by_id, insert_event

    stress = StressBreakdown(load=20, memory=10, thermal=5, latency=0, io=0, gpu=25, wakeups=8)
    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=stress,
        culprits=["gpu_process"],
        event_dir=None,
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.stress.gpu == 25
    assert retrieved.stress.wakeups == 8


def test_migrate_add_stress_columns(tmp_path: Path):
    """Migration adds stress_gpu and stress_wakeups columns to existing tables."""
    from pause_monitor.storage import migrate_add_stress_columns

    db_path = tmp_path / "legacy.db"

    # Create a v1 schema without stress_gpu/stress_wakeups columns
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE samples (
            id INTEGER PRIMARY KEY,
            timestamp REAL,
            interval REAL,
            stress_total INTEGER,
            stress_load INTEGER,
            stress_memory INTEGER,
            stress_thermal INTEGER,
            stress_latency INTEGER,
            stress_io INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            timestamp REAL,
            duration REAL,
            stress_total INTEGER,
            stress_load INTEGER,
            stress_memory INTEGER,
            stress_thermal INTEGER,
            stress_latency INTEGER,
            stress_io INTEGER,
            culprits TEXT
        )
    """)
    conn.commit()

    # Run migration
    migrate_add_stress_columns(conn)

    # Verify columns were added
    cursor = conn.execute("PRAGMA table_info(samples)")
    sample_columns = {row[1] for row in cursor.fetchall()}
    assert "stress_gpu" in sample_columns
    assert "stress_wakeups" in sample_columns

    cursor = conn.execute("PRAGMA table_info(events)")
    event_columns = {row[1] for row in cursor.fetchall()}
    assert "stress_gpu" in event_columns
    assert "stress_wakeups" in event_columns

    conn.close()


def test_migrate_add_stress_columns_idempotent(tmp_path: Path):
    """Running migration multiple times is safe."""
    from pause_monitor.storage import migrate_add_stress_columns

    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    # Run migration twice - should not raise
    migrate_add_stress_columns(conn)
    migrate_add_stress_columns(conn)
    conn.close()
