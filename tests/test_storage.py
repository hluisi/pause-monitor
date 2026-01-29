"""Tests for SQLite storage layer."""

import sqlite3
import time
from pathlib import Path

import pytest

from pause_monitor.storage import (
    SCHEMA_VERSION,
    get_schema_version,
    init_database,
)


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
    # Schema v8: per-process event tables
    assert "process_events" in table_names
    assert "process_snapshots" in table_names
    assert "daemon_state" in table_names


def test_init_database_sets_schema_version(tmp_path: Path):
    """init_database sets schema version in daemon_state."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    version = get_schema_version(conn)
    conn.close()
    assert version == SCHEMA_VERSION


# --- Prune Tests ---


def test_prune_rejects_zero_days(initialized_db: Path):
    """prune_old_data raises ValueError when days < 1."""
    from pause_monitor.storage import get_connection, prune_old_data

    conn = get_connection(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=0)

    conn.close()


def test_prune_rejects_negative_days(initialized_db: Path):
    """prune_old_data raises ValueError for negative retention days."""
    from pause_monitor.storage import get_connection, prune_old_data

    conn = get_connection(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=-5)

    conn.close()


def test_prune_deletes_old_events(initialized_db: Path):
    """prune_old_data deletes old closed process events."""
    from pause_monitor.storage import (
        close_process_event,
        create_process_event,
        get_connection,
        prune_old_data,
    )

    conn = get_connection(initialized_db)

    # Create old closed event (100 days ago)
    old_entry = time.time() - 100 * 86400
    old_exit = old_entry + 60
    event_id = create_process_event(
        conn,
        pid=123,
        command="old_proc",
        boot_time=1000000,
        entry_time=old_entry,
        entry_band="elevated",
        peak_score=50,
        peak_band="elevated",
        peak_snapshot="{}",
    )
    close_process_event(conn, event_id, old_exit)

    # Create recent closed event (10 days ago)
    recent_entry = time.time() - 10 * 86400
    recent_exit = recent_entry + 60
    event_id2 = create_process_event(
        conn,
        pid=456,
        command="recent_proc",
        boot_time=1000000,
        entry_time=recent_entry,
        entry_band="elevated",
        peak_score=50,
        peak_band="elevated",
        peak_snapshot="{}",
    )
    close_process_event(conn, event_id2, recent_exit)

    # Prune (30 day retention)
    events_deleted, samples_deleted = prune_old_data(conn, events_days=30)

    remaining = conn.execute("SELECT COUNT(*) FROM process_events").fetchone()[0]
    conn.close()

    assert events_deleted == 1
    assert samples_deleted == 0
    assert remaining == 1


def test_prune_preserves_open_events(initialized_db: Path):
    """prune_old_data does not delete open events regardless of age."""
    from pause_monitor.storage import create_process_event, get_connection, prune_old_data

    conn = get_connection(initialized_db)

    # Create old OPEN event (100 days ago, never closed)
    old_entry = time.time() - 100 * 86400
    create_process_event(
        conn,
        pid=123,
        command="old_open_proc",
        boot_time=1000000,
        entry_time=old_entry,
        entry_band="elevated",
        peak_score=50,
        peak_band="elevated",
        peak_snapshot="{}",
    )

    # Prune (1 day retention)
    events_deleted, samples_deleted = prune_old_data(conn, events_days=1)

    remaining = conn.execute("SELECT COUNT(*) FROM process_events").fetchone()[0]
    conn.close()

    assert events_deleted == 0
    assert samples_deleted == 0
    assert remaining == 1


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


def test_schema_version_9(initialized_db: Path):
    """Schema version should be 9."""
    from pause_monitor.storage import get_connection

    conn = get_connection(initialized_db)
    version = get_schema_version(conn)
    conn.close()
    assert version == 9


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


# --- System Samples Tests ---


def test_insert_and_get_system_sample(tmp_path):
    """insert_system_sample and get_last_system_sample_time work correctly."""
    from pause_monitor.storage import (
        get_connection,
        get_last_system_sample_time,
        init_database,
        insert_system_sample,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # No samples yet
    assert get_last_system_sample_time(conn) is None

    # Insert a sample
    insert_system_sample(conn, '{"max_score": 42}')

    # Now we should have a timestamp
    last_time = get_last_system_sample_time(conn)
    assert last_time is not None
    assert last_time > 0

    # Verify data stored
    row = conn.execute("SELECT data FROM system_samples").fetchone()
    assert row[0] == '{"max_score": 42}'

    conn.close()


def test_system_samples_pruning(tmp_path):
    """prune_old_data deletes old system samples."""
    import time

    from pause_monitor.storage import (
        get_connection,
        init_database,
        prune_old_data,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Insert old sample (10 days ago)
    old_time = time.time() - 10 * 86400
    conn.execute(
        "INSERT INTO system_samples (captured_at, data) VALUES (?, ?)",
        (old_time, '{"old": true}'),
    )
    conn.commit()

    # Insert recent sample (1 day ago)
    recent_time = time.time() - 1 * 86400
    conn.execute(
        "INSERT INTO system_samples (captured_at, data) VALUES (?, ?)",
        (recent_time, '{"recent": true}'),
    )
    conn.commit()

    # Prune with 7 day retention
    events_deleted, samples_deleted = prune_old_data(conn, system_samples_days=7)

    assert samples_deleted == 1  # Old one deleted

    # Verify only recent sample remains
    rows = conn.execute("SELECT data FROM system_samples").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == '{"recent": true}'

    conn.close()
