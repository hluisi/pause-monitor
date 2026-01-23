"""Tests for SQLite storage layer."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pause_monitor.storage import (
    SCHEMA_VERSION,
    Sample,
    create_event,
    finalize_event,
    get_schema_version,
    init_database,
)
from pause_monitor.stress import StressBreakdown


def create_test_event(
    conn: sqlite3.Connection,
    start_time: datetime | None = None,
    duration_seconds: float = 2.5,
    peak_stress: int = 15,
    peak_tier: int = 2,
    status: str = "unreviewed",
) -> int:
    """Create a test event using the new API."""
    from pause_monitor.storage import update_event_status

    if start_time is None:
        start_time = datetime.now()

    event_id = create_event(conn, start_time)
    finalize_event(
        conn,
        event_id,
        end_timestamp=start_time + timedelta(seconds=duration_seconds),
        peak_stress=peak_stress,
        peak_tier=peak_tier,
    )
    if status != "unreviewed":
        update_event_status(conn, event_id, status)
    return event_id


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
    """Event has correct fields for the new schema."""
    from pause_monitor.storage import Event

    now = datetime.now()
    event = Event(
        start_timestamp=now,
        end_timestamp=now + timedelta(seconds=3.5),
        peak_stress=15,
        peak_tier=2,
        status="unreviewed",
        notes="Test pause",
    )
    # Calculate duration from timestamps
    duration = (event.end_timestamp - event.start_timestamp).total_seconds()
    assert duration == 3.5
    assert event.peak_stress == 15
    assert event.peak_tier == 2


def test_create_and_finalize_event(initialized_db: Path):
    """create_event and finalize_event store event in database."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    start_time = datetime.now()
    event_id = create_event(conn, start_time)
    assert event_id > 0

    # Finalize the event
    end_time = start_time + timedelta(seconds=2.5)
    finalize_event(conn, event_id, end_timestamp=end_time, peak_stress=25, peak_tier=2)

    # Verify it was stored correctly
    event = get_event_by_id(conn, event_id)
    conn.close()

    assert event is not None
    assert event.start_timestamp == start_time
    assert event.end_timestamp == end_time
    assert event.peak_stress == 25
    assert event.peak_tier == 2


def test_get_events_by_timerange(initialized_db: Path):
    """get_events returns events within time range."""
    from pause_monitor.storage import get_events

    conn = sqlite3.connect(initialized_db)

    base_time = 1000000.0
    for i in range(5):
        start_time = datetime.fromtimestamp(base_time + i * 3600)
        create_test_event(conn, start_time=start_time, peak_stress=10 + i)

    events = get_events(
        conn,
        start=datetime.fromtimestamp(base_time + 3600),
        end=datetime.fromtimestamp(base_time + 10800),
    )
    conn.close()

    assert len(events) == 3


def test_get_event_by_id_found(initialized_db: Path):
    """get_event_by_id returns event when found."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    start_time = datetime.now()
    event_id = create_test_event(conn, start_time=start_time, peak_stress=30, peak_tier=2)

    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.id == event_id
    assert retrieved.peak_stress == 30
    assert retrieved.peak_tier == 2


def test_get_event_by_id_not_found(initialized_db: Path):
    """get_event_by_id returns None when not found."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    result = get_event_by_id(conn, 99999)
    conn.close()

    assert result is None


def test_prune_old_data_deletes_old_events(initialized_db: Path):
    """prune_old_data deletes old events with prunable status."""
    import time

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old event (100 days ago) with 'reviewed' status (prunable)
    old_time = datetime.fromtimestamp(time.time() - 100 * 86400)
    create_test_event(conn, start_time=old_time, status="reviewed")

    # Insert recent event (30 days ago) - also reviewed but within retention
    recent_time = datetime.fromtimestamp(time.time() - 30 * 86400)
    create_test_event(conn, start_time=recent_time, status="reviewed")

    # Prune events older than 90 days
    events_deleted = prune_old_data(conn, events_days=90)

    # Verify old event was deleted, recent kept
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert events_deleted == 1
    assert count == 1


def test_prune_only_deletes_prunable_events(initialized_db: Path):
    """prune_old_data only deletes reviewed/dismissed events, not unreviewed/pinned."""
    import time

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert old events of each status
    old_time = datetime.fromtimestamp(time.time() - 100 * 86400)
    create_test_event(conn, start_time=old_time, status="unreviewed")  # Should NOT be pruned
    create_test_event(conn, start_time=old_time, status="pinned")  # Should NOT be pruned
    create_test_event(conn, start_time=old_time, status="reviewed")  # Should be pruned
    create_test_event(conn, start_time=old_time, status="dismissed")  # Should be pruned

    # Prune events older than 90 days
    events_deleted = prune_old_data(conn, events_days=90)

    # Only reviewed and dismissed should be pruned
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert events_deleted == 2
    assert count == 2  # unreviewed and pinned remain


def test_prune_deletes_event_samples_with_events(initialized_db: Path):
    """prune_old_data deletes event_samples linked to pruned events."""
    import time

    from pause_monitor.storage import EventSample, insert_event_sample, prune_old_data
    from pause_monitor.stress import StressBreakdown

    conn = sqlite3.connect(initialized_db)

    # Insert old event (100 days ago) with reviewed status (prunable)
    old_time = datetime.fromtimestamp(time.time() - 100 * 86400)
    event_id = create_test_event(conn, start_time=old_time, status="reviewed")

    # Insert an event sample linked to the event
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    sample = EventSample(
        event_id=event_id,
        timestamp=old_time,
        tier=2,
        elapsed_ns=100_000_000,
        throttled=False,
        cpu_power=5.0,
        gpu_pct=10.0,
        gpu_power=1.0,
        io_read_per_s=1000.0,
        io_write_per_s=500.0,
        wakeups_per_s=50.0,
        pageins_per_s=0.0,
        stress=stress,
        top_cpu_procs=[],
        top_pagein_procs=[],
        top_wakeup_procs=[],
        top_diskio_procs=[],
    )
    insert_event_sample(conn, sample)

    # Verify event sample exists
    sample_count_before = conn.execute("SELECT COUNT(*) FROM event_samples").fetchone()[0]
    assert sample_count_before == 1

    # Prune
    events_deleted = prune_old_data(conn, events_days=90)

    # Verify event sample was deleted along with event
    sample_count_after = conn.execute("SELECT COUNT(*) FROM event_samples").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert events_deleted == 1
    assert sample_count_after == 0
    assert event_count == 0


def test_prune_with_nothing_to_delete(initialized_db: Path):
    """prune_old_data returns zero when nothing to delete."""
    import time

    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    # Insert recent event (10 days ago) - within retention
    recent_time = datetime.fromtimestamp(time.time() - 10 * 86400)
    create_test_event(conn, start_time=recent_time, status="reviewed")

    # Prune with 90 day retention
    events_deleted = prune_old_data(conn, events_days=90)
    conn.close()

    assert events_deleted == 0


def test_prune_rejects_zero_days(initialized_db: Path):
    """prune_old_data raises ValueError when events_days < 1."""
    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=0)

    conn.close()


def test_prune_rejects_negative_days(initialized_db: Path):
    """prune_old_data raises ValueError for negative retention days."""
    from pause_monitor.storage import prune_old_data

    conn = sqlite3.connect(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=-5)

    conn.close()


# --- Event Status Tests ---


def test_event_dataclass_has_status_field():
    """Event dataclass has status field with default value."""
    from pause_monitor.storage import Event

    event = Event(
        start_timestamp=datetime.now(),
        end_timestamp=datetime.now() + timedelta(seconds=3.5),
        peak_stress=15,
        peak_tier=2,
    )
    # Default should be "unreviewed"
    assert event.status == "unreviewed"
    assert event.notes is None


def test_event_dataclass_accepts_status():
    """Event dataclass accepts explicit status value."""
    from pause_monitor.storage import Event

    event = Event(
        start_timestamp=datetime.now(),
        end_timestamp=datetime.now() + timedelta(seconds=3.5),
        peak_stress=15,
        peak_tier=2,
        status="pinned",
        notes="Important event",
    )
    assert event.status == "pinned"
    assert event.notes == "Important event"


def test_create_event_with_status(initialized_db: Path):
    """Events are created with default unreviewed status."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn, status="unreviewed")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "unreviewed"


def test_create_event_with_pinned_status(initialized_db: Path):
    """Events can have pinned status set via update."""
    from pause_monitor.storage import get_event_by_id, update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)

    # Update to pinned with notes
    update_event_status(conn, event_id, "pinned", "Keep this one")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "pinned"
    assert retrieved.notes == "Keep this one"


def test_update_event_status(initialized_db: Path):
    """Event status can be updated."""
    from pause_monitor.storage import get_event_by_id, update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)

    # Update status only
    update_event_status(conn, event_id, "reviewed")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "reviewed"


def test_update_event_status_with_notes(initialized_db: Path):
    """Event status can be updated with notes."""
    from pause_monitor.storage import get_event_by_id, update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)

    # Update status with notes
    update_event_status(conn, event_id, "pinned", "Chrome memory leak")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "pinned"
    assert retrieved.notes == "Chrome memory leak"


def test_update_event_status_preserves_notes(initialized_db: Path):
    """Updating status without notes preserves existing notes."""
    from pause_monitor.storage import get_event_by_id, update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)

    # First set notes
    update_event_status(conn, event_id, "unreviewed", "Original note")

    # Update status only (notes=None should preserve existing notes)
    update_event_status(conn, event_id, "reviewed")
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.status == "reviewed"
    assert retrieved.notes == "Original note"


def test_update_event_status_invalid_status(initialized_db: Path):
    """update_event_status rejects invalid status values."""
    from pause_monitor.storage import update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)

    # Invalid status should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        update_event_status(conn, event_id, "invalid_status")

    assert "Invalid status 'invalid_status'" in str(exc_info.value)
    assert "dismissed" in str(exc_info.value)  # Shows valid options
    conn.close()


def test_get_events_returns_status(initialized_db: Path):
    """get_events includes status field in results."""
    from pause_monitor.storage import get_events, update_event_status

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn)
    update_event_status(conn, event_id, "pinned", "Test note")

    events = get_events(conn, limit=10)
    conn.close()

    assert len(events) == 1
    assert events[0].status == "pinned"
    assert events[0].notes == "Test note"


# --- Status-Aware Pruning Tests ---


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


def test_event_stores_peak_stress(initialized_db: Path):
    """Events store and retrieve peak_stress correctly."""
    from pause_monitor.storage import get_event_by_id

    conn = sqlite3.connect(initialized_db)
    event_id = create_test_event(conn, peak_stress=68, peak_tier=3)
    retrieved = get_event_by_id(conn, event_id)
    conn.close()

    assert retrieved is not None
    assert retrieved.peak_stress == 68
    assert retrieved.peak_tier == 3


def test_get_events_returns_peak_stress(tmp_path):
    """get_events includes peak_stress and peak_tier in results."""
    from pause_monitor.storage import get_event_by_id, get_events

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = sqlite3.connect(db_path)

    event_id = create_test_event(conn, peak_stress=35, peak_tier=2)
    assert event_id > 0

    events = get_events(conn, limit=1)
    assert len(events) == 1
    assert events[0].peak_stress == 35
    assert events[0].peak_tier == 2

    # Also verify get_event_by_id reads peak_stress
    event_by_id = get_event_by_id(conn, event_id)
    assert event_by_id is not None
    assert event_by_id.peak_stress == 35
    assert event_by_id.peak_tier == 2

    conn.close()
