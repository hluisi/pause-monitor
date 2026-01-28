"""Tests for SQLite storage layer."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pause_monitor.storage import (
    SCHEMA_VERSION,
    create_event,
    finalize_event,
    get_schema_version,
    init_database,
)


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
    # Schema v8: new per-process event tables
    assert "process_events" in table_names
    assert "process_snapshots" in table_names
    assert "process_sample_records" in table_names
    assert "daemon_state" in table_names


def test_init_database_sets_schema_version(tmp_path: Path):
    """init_database sets schema version in daemon_state."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    version = get_schema_version(conn)
    conn.close()
    assert version == SCHEMA_VERSION


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


# --- Schema v7: JSON Blob Storage Tests ---


def test_schema_version_8(initialized_db: Path):
    """Schema version should be 8."""
    from pause_monitor.storage import get_connection

    conn = get_connection(initialized_db)
    version = get_schema_version(conn)
    conn.close()
    assert version == 8


def test_insert_process_sample_json(initialized_db: Path):
    """Process sample should be stored as JSON."""
    from pause_monitor.collector import ProcessSamples, ProcessScore
    from pause_monitor.storage import (
        get_connection,
        get_process_samples,
        insert_process_sample,
    )

    conn = get_connection(initialized_db)

    event_id = create_event(conn, datetime.now())

    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1050,
        process_count=500,
        max_score=75,
        rogues=[
            ProcessScore(
                pid=1,
                command="test",
                cpu=80.0,
                state="running",
                mem=1000,
                cmprs=0,
                pageins=0,
                csw=10,
                sysbsd=5,
                threads=2,
                score=75,
                categories=frozenset({"cpu"}),
                captured_at=1706000000.0,
            ),
        ],
    )

    insert_process_sample(conn, event_id, tier=2, samples=samples)

    retrieved = get_process_samples(conn, event_id)
    conn.close()

    assert len(retrieved) == 1
    assert retrieved[0].data.max_score == 75
    assert retrieved[0].data.rogues[0].command == "test"


def test_process_sample_record_dataclass():
    """ProcessSampleRecord has correct fields."""
    from pause_monitor.collector import ProcessSamples
    from pause_monitor.storage import ProcessSampleRecord

    samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=500,
        process_count=100,
        max_score=50,
        rogues=[],
    )
    record = ProcessSampleRecord(
        id=1,
        event_id=10,
        tier=2,
        data=samples,
    )
    assert record.id == 1
    assert record.event_id == 10
    assert record.tier == 2
    assert record.data.max_score == 50


def test_get_process_samples_empty(initialized_db: Path):
    """get_process_samples returns empty list when no samples exist."""
    from pause_monitor.storage import get_connection, get_process_samples

    conn = get_connection(initialized_db)
    event_id = create_event(conn, datetime.now())

    samples = get_process_samples(conn, event_id)
    conn.close()

    assert samples == []


def test_insert_multiple_process_samples(initialized_db: Path):
    """Multiple process samples can be inserted and retrieved."""
    from pause_monitor.collector import ProcessSamples, ProcessScore
    from pause_monitor.storage import (
        get_connection,
        get_process_samples,
        insert_process_sample,
    )

    conn = get_connection(initialized_db)
    event_id = create_event(conn, datetime.now())

    for i in range(3):
        samples = ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=100 * (i + 1),
            process_count=100 + i,
            max_score=50 + i * 10,
            rogues=[
                ProcessScore(
                    pid=i + 1,
                    command=f"proc{i}",
                    cpu=float(i * 10),
                    state="running",
                    mem=1000,
                    cmprs=0,
                    pageins=0,
                    csw=10,
                    sysbsd=5,
                    threads=2,
                    score=50 + i * 10,
                    categories=frozenset({"cpu"}),
                    captured_at=1706000000.0 + i,
                ),
            ],
        )
        insert_process_sample(conn, event_id, tier=2, samples=samples)

    retrieved = get_process_samples(conn, event_id)
    conn.close()

    assert len(retrieved) == 3
    assert retrieved[0].data.elapsed_ms == 100
    assert retrieved[1].data.elapsed_ms == 200
    assert retrieved[2].data.elapsed_ms == 300


# --- Daemon State Tests ---


def test_get_daemon_state_missing_key(tmp_path):
    """get_daemon_state returns None for missing key."""
    from pause_monitor.storage import get_connection, get_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    value = get_daemon_state(conn, "nonexistent")
    conn.close()
    assert value is None


def test_set_and_get_daemon_state(tmp_path):
    """set_daemon_state stores value, get_daemon_state retrieves it."""
    from pause_monitor.storage import get_connection, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    set_daemon_state(conn, "boot_time", "1706000000")
    value = get_daemon_state(conn, "boot_time")
    conn.close()
    assert value == "1706000000"


def test_set_daemon_state_overwrites(tmp_path):
    """set_daemon_state overwrites existing value."""
    from pause_monitor.storage import get_connection, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    set_daemon_state(conn, "boot_time", "1000")
    set_daemon_state(conn, "boot_time", "2000")
    value = get_daemon_state(conn, "boot_time")
    conn.close()
    assert value == "2000"


def test_get_daemon_state_no_table(tmp_path):
    """get_daemon_state returns None when table doesn't exist."""
    from pause_monitor.storage import get_daemon_state

    # Create empty database without schema
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    value = get_daemon_state(conn, "any_key")
    conn.close()
    assert value is None


# --- Schema v8: Per-Process Event Tables ---


def test_schema_has_process_events_table(tmp_path):
    """Schema includes process_events table."""
    from pause_monitor.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_events'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_schema_has_process_snapshots_table(tmp_path):
    """Schema includes process_snapshots table."""
    from pause_monitor.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_snapshots'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_schema_no_legacy_events_table(tmp_path):
    """Schema does not have legacy events table."""
    from pause_monitor.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
    assert cursor.fetchone() is None
    conn.close()


def test_process_events_table_structure(tmp_path):
    """process_events has expected columns."""
    from pause_monitor.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute("PRAGMA table_info(process_events)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected = {
        "id",
        "pid",
        "command",
        "boot_time",
        "entry_time",
        "exit_time",
        "entry_band",
        "peak_band",
        "peak_score",
        "peak_snapshot",
    }
    assert expected.issubset(columns)


def test_process_snapshots_table_structure(tmp_path):
    """process_snapshots has expected columns."""
    from pause_monitor.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute("PRAGMA table_info(process_snapshots)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected = {"id", "event_id", "snapshot_type", "snapshot"}
    assert expected.issubset(columns)


# --- Process Event CRUD Tests ---


def test_create_process_event(tmp_path):
    """create_process_event inserts and returns event ID."""
    from pause_monitor.storage import create_process_event, get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test_cmd",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot='{"pid": 123, "score": 45}',
    )

    assert event_id is not None
    assert isinstance(event_id, int)
    conn.close()


def test_get_open_events(tmp_path):
    """get_open_events returns events with no exit_time."""
    from pause_monitor.storage import (
        create_process_event,
        get_connection,
        get_open_events,
        init_database,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    create_process_event(
        conn,
        pid=123,
        command="open",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot="{}",
    )

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123
    conn.close()


def test_close_process_event(tmp_path):
    """close_process_event sets exit_time."""
    from pause_monitor.storage import (
        close_process_event,
        create_process_event,
        get_connection,
        get_open_events,
        init_database,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot="{}",
    )

    close_process_event(conn, event_id, exit_time=1706000200.5)

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 0
    conn.close()


def test_update_process_event_peak(tmp_path):
    """update_process_event_peak updates peak fields."""
    from pause_monitor.storage import (
        create_process_event,
        get_connection,
        init_database,
        update_process_event_peak,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot='{"score": 45}',
    )

    update_process_event_peak(
        conn, event_id, peak_score=80, peak_band="critical", peak_snapshot='{"score": 80}'
    )

    row = conn.execute(
        "SELECT peak_score, peak_band FROM process_events WHERE id = ?", (event_id,)
    ).fetchone()
    assert row[0] == 80
    assert row[1] == "critical"
    conn.close()


def test_insert_process_snapshot(tmp_path):
    """insert_process_snapshot adds snapshot to event."""
    from pause_monitor.storage import (
        create_process_event,
        get_connection,
        init_database,
        insert_process_snapshot,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=1706000100.5,
        entry_band="elevated",
        peak_score=45,
        peak_band="elevated",
        peak_snapshot="{}",
    )

    insert_process_snapshot(conn, event_id, snapshot_type="entry", snapshot='{"score": 45}')

    row = conn.execute(
        "SELECT snapshot_type, snapshot FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    assert row[0] == "entry"
    assert row[1] == '{"score": 45}'
    conn.close()
