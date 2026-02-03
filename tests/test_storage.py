"""Tests for SQLite storage layer."""

import sqlite3
import time
from pathlib import Path

import pytest

from rogue_hunter.storage import (
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
    # Core tables
    assert "process_events" in table_names
    assert "process_snapshots" in table_names
    assert "daemon_state" in table_names
    # Forensic tables (schema v10)
    assert "forensic_captures" in table_names
    assert "spindump_processes" in table_names
    assert "spindump_threads" in table_names
    assert "log_entries" in table_names
    assert "buffer_context" in table_names


def test_init_database_sets_schema_version(tmp_path: Path):
    """init_database sets schema version in daemon_state."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    version = get_schema_version(conn)
    conn.close()
    assert version == SCHEMA_VERSION


def test_init_database_recreates_on_version_mismatch(tmp_path: Path):
    """init_database deletes and recreates DB when schema version differs."""
    db_path = tmp_path / "test.db"

    # Create DB with old schema version
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE daemon_state (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)")
    conn.execute(
        "INSERT INTO daemon_state (key, value, updated_at) VALUES ('schema_version', '1', 0)"
    )
    conn.execute("CREATE TABLE old_table (id INTEGER)")
    conn.execute("INSERT INTO old_table VALUES (42)")
    conn.commit()
    conn.close()

    # init_database should detect mismatch and recreate
    init_database(db_path)

    # Verify new schema
    conn = sqlite3.connect(db_path)
    version = get_schema_version(conn)
    assert version == SCHEMA_VERSION

    # Old table should be gone
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t[0] for t in tables]
    assert "old_table" not in table_names
    assert "process_events" in table_names
    conn.close()


def test_init_database_preserves_matching_schema(tmp_path: Path):
    """init_database keeps existing data when schema version matches."""
    db_path = tmp_path / "test.db"

    # Create DB with current schema
    init_database(db_path)

    # Add some data
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daemon_state (key, value, updated_at) VALUES ('test_key', 'test_value', 0)"
    )
    conn.commit()
    conn.close()

    # Re-init should preserve data
    init_database(db_path)

    # Verify data preserved
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM daemon_state WHERE key = 'test_key'").fetchone()
    conn.close()
    assert row[0] == "test_value"


# --- Prune Tests ---


def test_prune_rejects_zero_days(initialized_db: Path):
    """prune_old_data raises ValueError when days < 1."""
    from rogue_hunter.storage import get_connection, prune_old_data

    conn = get_connection(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=0)

    conn.close()


def test_prune_rejects_negative_days(initialized_db: Path):
    """prune_old_data raises ValueError for negative retention days."""
    from rogue_hunter.storage import get_connection, prune_old_data

    conn = get_connection(initialized_db)

    with pytest.raises(ValueError, match="Retention days must be >= 1"):
        prune_old_data(conn, events_days=-5)

    conn.close()


def test_prune_deletes_old_events(initialized_db: Path):
    """prune_old_data deletes old closed process events."""
    from rogue_hunter.storage import (
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
    )
    close_process_event(conn, event_id2, recent_exit)

    # Prune (30 day retention)
    events_deleted = prune_old_data(conn, events_days=30)

    remaining = conn.execute("SELECT COUNT(*) FROM process_events").fetchone()[0]
    conn.close()

    assert events_deleted == 1
    assert remaining == 1


def test_prune_preserves_open_events(initialized_db: Path):
    """prune_old_data does not delete open events regardless of age."""
    from rogue_hunter.storage import create_process_event, get_connection, prune_old_data

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
    )

    # Prune (1 day retention)
    events_deleted = prune_old_data(conn, events_days=1)

    remaining = conn.execute("SELECT COUNT(*) FROM process_events").fetchone()[0]
    conn.close()

    assert events_deleted == 0
    assert remaining == 1


# --- Daemon State Tests ---


def test_get_daemon_state_missing_key(tmp_path):
    """get_daemon_state returns None for missing key."""
    from rogue_hunter.storage import get_connection, get_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    value = get_daemon_state(conn, "nonexistent")
    conn.close()
    assert value is None


def test_set_and_get_daemon_state(tmp_path):
    """set_daemon_state stores value, get_daemon_state retrieves it."""
    from rogue_hunter.storage import get_connection, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    set_daemon_state(conn, "boot_time", "1706000000")
    value = get_daemon_state(conn, "boot_time")
    conn.close()
    assert value == "1706000000"


def test_set_daemon_state_overwrites(tmp_path):
    """set_daemon_state overwrites existing value."""
    from rogue_hunter.storage import get_connection, get_daemon_state, set_daemon_state

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    set_daemon_state(conn, "boot_time", "1000")
    set_daemon_state(conn, "boot_time", "2000")
    value = get_daemon_state(conn, "boot_time")
    conn.close()
    assert value == "2000"


# --- Schema v10: Process Event Tables ---


def test_schema_has_process_events_table(tmp_path):
    """Schema includes process_events table."""
    from rogue_hunter.storage import get_connection, init_database

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
    from rogue_hunter.storage import get_connection, init_database

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_snapshots'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_schema_version_is_19():
    """Schema version is 19 for machine snapshots."""
    from rogue_hunter.storage import SCHEMA_VERSION

    assert SCHEMA_VERSION == 19


def test_process_snapshots_has_resource_shares():
    """process_snapshots table has resource share columns."""
    from rogue_hunter.storage import SCHEMA

    # New columns should exist
    assert "cpu_share" in SCHEMA
    assert "gpu_share" in SCHEMA
    assert "mem_share" in SCHEMA
    assert "disk_share" in SCHEMA
    assert "wakeups_share" in SCHEMA
    assert "disproportionality" in SCHEMA
    assert "dominant_resource" in SCHEMA

    # Old columns should not exist
    assert "blocking_score" not in SCHEMA
    assert "contention_score" not in SCHEMA
    assert "pressure_score" not in SCHEMA
    assert "efficiency_score" not in SCHEMA
    assert "dominant_category" not in SCHEMA
    assert "dominant_metrics" not in SCHEMA


# --- Process Event CRUD Tests ---


def test_create_process_event(tmp_path):
    """create_process_event inserts and returns event ID."""
    from rogue_hunter.storage import create_process_event, get_connection, init_database

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
    )

    assert event_id is not None
    assert isinstance(event_id, int)

    # Verify peak_snapshot_id is NULL initially
    row = conn.execute(
        "SELECT peak_snapshot_id FROM process_events WHERE id = ?", (event_id,)
    ).fetchone()
    assert row[0] is None
    conn.close()


def test_get_open_events(tmp_path):
    """get_open_events returns events with no exit_time."""
    from rogue_hunter.storage import (
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
    )

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123
    assert "peak_snapshot_id" in events[0]
    conn.close()


def test_close_process_event(tmp_path):
    """close_process_event sets exit_time."""
    from rogue_hunter.storage import (
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
    )

    close_process_event(conn, event_id, exit_time=1706000200.5)

    events = get_open_events(conn, boot_time=1706000000)
    assert len(events) == 0
    conn.close()


def test_update_process_event_peak(tmp_path):
    """update_process_event_peak updates peak fields including peak_snapshot_id."""
    from rogue_hunter.storage import (
        create_process_event,
        get_connection,
        init_database,
        insert_process_snapshot,
        update_process_event_peak,
    )
    from tests.conftest import make_process_score

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
    )

    # Insert a snapshot and use its ID
    score = make_process_score(pid=123, command="test", score=80)
    snapshot_id = insert_process_snapshot(conn, event_id, "checkpoint", score)

    update_process_event_peak(
        conn, event_id, peak_score=80, peak_band="critical", peak_snapshot_id=snapshot_id
    )

    row = conn.execute(
        "SELECT peak_score, peak_band, peak_snapshot_id FROM process_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    assert row[0] == 80
    assert row[1] == "critical"
    assert row[2] == snapshot_id
    conn.close()


def test_insert_process_snapshot(tmp_path):
    """insert_process_snapshot adds structured snapshot and returns ID."""
    from rogue_hunter.storage import (
        create_process_event,
        get_connection,
        get_process_snapshots,
        init_database,
        insert_process_snapshot,
    )
    from tests.conftest import make_process_score

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
    )

    score = make_process_score(pid=123, command="test", score=45, cpu=30.5, mem=200)
    snapshot_id = insert_process_snapshot(conn, event_id, snapshot_type="entry", score=score)

    assert snapshot_id is not None
    assert isinstance(snapshot_id, int)

    # Verify structured columns with plain values
    snapshots = get_process_snapshots(conn, event_id)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap["snapshot_type"] == "entry"
    # Fields are now plain values
    assert snap["score"] == 45
    assert snap["cpu"] == 30.5
    assert snap["mem"] == 200
    # Resource-based scoring
    assert snap["dominant_resource"] == "cpu"
    assert isinstance(snap["disproportionality"], float)
    conn.close()


# --- Forensic Capture Tests ---


def test_create_forensic_capture(tmp_path):
    """create_forensic_capture inserts and returns capture ID."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_connection,
        init_database,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Create parent event first
    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )

    capture_id = create_forensic_capture(conn, event_id, trigger="band_entry_high")

    assert capture_id is not None
    assert isinstance(capture_id, int)
    conn.close()


def test_get_forensic_captures(tmp_path):
    """get_forensic_captures returns captures for an event."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_connection,
        get_forensic_captures,
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
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )

    create_forensic_capture(conn, event_id, trigger="test1")
    create_forensic_capture(conn, event_id, trigger="test2")

    captures = get_forensic_captures(conn, event_id)
    assert len(captures) == 2
    assert captures[0]["trigger"] == "test1"
    assert captures[1]["trigger"] == "test2"
    conn.close()


def test_insert_and_get_spindump_process(tmp_path):
    """insert_spindump_process and get_spindump_processes work correctly."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_connection,
        get_spindump_processes,
        init_database,
        insert_spindump_process,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )
    capture_id = create_forensic_capture(conn, event_id, trigger="test")

    proc_id = insert_spindump_process(
        conn,
        capture_id=capture_id,
        pid=456,
        name="chrome",
        path="/Applications/Chrome.app",
        footprint_mb=500.5,
        thread_count=42,
    )

    assert proc_id is not None

    procs = get_spindump_processes(conn, capture_id)
    assert len(procs) == 1
    assert procs[0]["pid"] == 456
    assert procs[0]["name"] == "chrome"
    assert procs[0]["footprint_mb"] == 500.5
    conn.close()


def test_insert_and_get_spindump_threads(tmp_path):
    """insert_spindump_thread and get_spindump_threads work correctly."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_connection,
        get_spindump_threads,
        init_database,
        insert_spindump_process,
        insert_spindump_thread,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )
    capture_id = create_forensic_capture(conn, event_id, trigger="test")
    proc_id = insert_spindump_process(conn, capture_id, pid=456, name="chrome")

    insert_spindump_thread(
        conn,
        process_id=proc_id,
        thread_id="0x1234",
        thread_name="main-thread",
        sample_count=100,
        state="blocked_kevent",
    )

    threads = get_spindump_threads(conn, proc_id)
    assert len(threads) == 1
    assert threads[0]["thread_id"] == "0x1234"
    assert threads[0]["thread_name"] == "main-thread"
    assert threads[0]["state"] == "blocked_kevent"
    conn.close()


def test_insert_and_get_log_entries(tmp_path):
    """insert_log_entry and get_log_entries work correctly."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_connection,
        get_log_entries,
        init_database,
        insert_log_entry,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )
    capture_id = create_forensic_capture(conn, event_id, trigger="test")

    insert_log_entry(
        conn,
        capture_id=capture_id,
        timestamp="2024-01-15 10:30:45",
        event_message="Test error occurred",
        subsystem="com.apple.kernel",
        message_type="Error",
    )

    entries = get_log_entries(conn, capture_id)
    assert len(entries) == 1
    assert entries[0]["timestamp"] == "2024-01-15 10:30:45"
    assert entries[0]["event_message"] == "Test error occurred"
    assert entries[0]["subsystem"] == "com.apple.kernel"
    conn.close()


def test_insert_and_get_buffer_context(tmp_path):
    """insert_buffer_context and get_buffer_context work correctly."""
    import json

    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_buffer_context,
        get_connection,
        init_database,
        insert_buffer_context,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )
    capture_id = create_forensic_capture(conn, event_id, trigger="test")

    culprits = [{"pid": 123, "command": "chrome", "score": 85}]
    insert_buffer_context(
        conn,
        capture_id=capture_id,
        sample_count=30,
        peak_score=85,
        culprits=json.dumps(culprits),
    )

    context = get_buffer_context(conn, capture_id)
    assert context is not None
    assert context["sample_count"] == 30
    assert context["peak_score"] == 85
    assert json.loads(context["culprits"]) == culprits
    conn.close()


def test_forensic_cascade_delete(tmp_path):
    """Deleting a process event cascades to forensic data."""
    from rogue_hunter.storage import (
        create_forensic_capture,
        create_process_event,
        get_buffer_context,
        get_connection,
        get_forensic_captures,
        init_database,
        insert_buffer_context,
    )

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    event_id = create_process_event(
        conn,
        pid=123,
        command="test",
        boot_time=1706000000,
        entry_time=time.time(),
        entry_band="high",
        peak_score=85,
        peak_band="high",
    )
    capture_id = create_forensic_capture(conn, event_id, trigger="test")
    insert_buffer_context(conn, capture_id, sample_count=30, peak_score=85, culprits="[]")

    # Delete the event
    conn.execute("DELETE FROM process_events WHERE id = ?", (event_id,))
    conn.commit()

    # Forensic data should be gone due to CASCADE
    assert get_forensic_captures(conn, event_id) == []
    assert get_buffer_context(conn, capture_id) is None
    conn.close()
