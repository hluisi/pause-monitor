# tests/test_tracker.py
"""Tests for per-process band tracker."""

import json

from pause_monitor.collector import ProcessScore


def make_score(
    pid: int = 123,
    command: str = "test",
    score: int = 50,
    captured_at: float = 1706000100.0,
    **kwargs,
) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    defaults = {
        "cpu": 50.0,
        "state": "running",
        "mem": 1000,
        "cmprs": 0,
        "pageins": 0,
        "csw": 10,
        "sysbsd": 5,
        "threads": 2,
        "categories": frozenset(["cpu"]) if score >= 40 else frozenset(),
    }
    defaults.update(kwargs)
    return ProcessScore(
        pid=pid,
        command=command,
        score=score,
        captured_at=captured_at,
        **defaults,
    )


def test_tracker_creates_event_on_threshold_crossing(tmp_path):
    """ProcessTracker creates event when score crosses tracking threshold."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()  # tracking_band="elevated", threshold=40
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Score below threshold — no event
    tracker.update([make_score(pid=123, score=30, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 0

    # Score above threshold — event created
    tracker.update([make_score(pid=123, score=50, captured_at=1706000101.0)])
    events = get_open_events(conn, 1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123

    conn.close()


def test_tracker_closes_event_when_score_drops(tmp_path):
    """ProcessTracker closes event when score drops below threshold."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Exit bad state
    tracker.update([make_score(pid=123, score=30, captured_at=1706000200.0)])
    assert len(get_open_events(conn, 1706000000)) == 0

    conn.close()


def test_tracker_updates_peak(tmp_path):
    """ProcessTracker updates peak when score increases."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter at 50
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])

    # Peak at 80
    tracker.update([make_score(pid=123, score=80, captured_at=1706000101.0)])

    row = conn.execute(
        "SELECT peak_score, peak_band FROM process_events WHERE pid = 123"
    ).fetchone()
    assert row[0] == 80
    assert row[1] == "critical"

    conn.close()


def test_tracker_closes_missing_pids(tmp_path):
    """ProcessTracker closes events for PIDs no longer in scores."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # PID 123 enters bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 1

    # PID 123 disappears from scores (process ended or no longer selected)
    tracker.update([])
    assert len(get_open_events(conn, 1706000000)) == 0

    conn.close()


def test_tracker_restores_state_from_db(tmp_path):
    """ProcessTracker restores tracking state from open events on init."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import (
        create_process_event,
        get_connection,
        init_database,
    )
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Pre-create an open event in DB
    create_process_event(
        conn,
        pid=456,
        command="preexisting",
        boot_time=1706000000,
        entry_time=1706000050.0,
        entry_band="elevated",
        peak_score=60,
        peak_band="high",
        peak_snapshot='{"pid": 456}',
    )

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Tracker should have loaded the open event
    assert 456 in tracker.tracked
    assert tracker.tracked[456].peak_score == 60

    # If we update with that PID still in bad state, it should update peak
    tracker.update([make_score(pid=456, command="preexisting", score=85, captured_at=1706000200.0)])

    row = conn.execute(
        "SELECT peak_score, peak_band FROM process_events WHERE pid = 456"
    ).fetchone()
    assert row[0] == 85
    assert row[1] == "critical"

    conn.close()


def test_tracker_inserts_entry_snapshot(tmp_path):
    """ProcessTracker inserts entry snapshot when event opens."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    score = make_score(
        pid=789,
        command="snap_test",
        score=55,
        captured_at=1706000100.0,
        cpu=60.0,
        mem=2000,
        cmprs=100,
        pageins=50,
        csw=20,
        sysbsd=10,
        threads=4,
        categories=frozenset(["cpu", "mem"]),
    )
    tracker.update([score])

    # Get the event ID
    event_id = tracker.tracked[789].event_id

    # Check snapshot was inserted
    row = conn.execute(
        "SELECT snapshot_type, snapshot FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "entry"
    snapshot = json.loads(row[1])
    assert snapshot["pid"] == 789
    assert snapshot["command"] == "snap_test"
    assert snapshot["score"] == 55

    conn.close()


def test_tracker_inserts_exit_snapshot_on_score_drop(tmp_path):
    """ProcessTracker inserts exit snapshot when score drops below threshold."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Exit bad state with score drop
    tracker.update([make_score(pid=123, score=30, captured_at=1706000200.0)])

    # Check both entry and exit snapshots exist
    rows = conn.execute(
        """SELECT snapshot_type, snapshot FROM process_snapshots
        WHERE event_id = ? ORDER BY snapshot_type""",
        (event_id,),
    ).fetchall()
    assert len(rows) == 2
    types = {row[0] for row in rows}
    assert types == {"entry", "exit"}

    # Verify exit snapshot has the low score
    exit_row = [row for row in rows if row[0] == "exit"][0]
    exit_snapshot = json.loads(exit_row[1])
    assert exit_snapshot["score"] == 30

    conn.close()


def test_tracker_no_exit_snapshot_for_disappeared_pid(tmp_path):
    """ProcessTracker does NOT insert exit snapshot when PID disappears."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # PID disappears (empty update)
    tracker.update([])

    # Only entry snapshot should exist (no exit snapshot since we don't have final state)
    rows = conn.execute(
        "SELECT snapshot_type FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "entry"

    conn.close()


def test_tracker_does_not_update_peak_for_equal_score(tmp_path):
    """ProcessTracker does NOT update peak when new score equals current peak."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter at 50
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])

    # Get initial peak_snapshot
    initial_snapshot = conn.execute(
        "SELECT peak_snapshot FROM process_events WHERE pid = 123"
    ).fetchone()[0]

    # Update with same score (different timestamp)
    tracker.update([make_score(pid=123, score=50, captured_at=1706000200.0)])

    # Peak snapshot should be unchanged (same object, not updated)
    new_snapshot = conn.execute(
        "SELECT peak_snapshot FROM process_events WHERE pid = 123"
    ).fetchone()[0]
    assert initial_snapshot == new_snapshot
    # Verify timestamp in snapshot is still the original
    snapshot_data = json.loads(new_snapshot)
    assert snapshot_data["captured_at"] == 1706000100.0

    conn.close()


def test_tracker_handles_multiple_simultaneous_processes(tmp_path):
    """ProcessTracker tracks multiple PIDs simultaneously."""
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Three processes enter bad state at once
    tracker.update(
        [
            make_score(pid=100, command="proc_a", score=50, captured_at=1706000100.0),
            make_score(pid=200, command="proc_b", score=60, captured_at=1706000100.0),
            make_score(pid=300, command="proc_c", score=70, captured_at=1706000100.0),
        ]
    )

    events = get_open_events(conn, 1706000000)
    assert len(events) == 3
    tracked_pids = {e["pid"] for e in events}
    assert tracked_pids == {100, 200, 300}

    # PID 200 drops below threshold, others remain
    tracker.update(
        [
            make_score(pid=100, command="proc_a", score=55, captured_at=1706000200.0),
            make_score(pid=200, command="proc_b", score=30, captured_at=1706000200.0),
            make_score(pid=300, command="proc_c", score=80, captured_at=1706000200.0),
        ]
    )

    events = get_open_events(conn, 1706000000)
    assert len(events) == 2
    tracked_pids = {e["pid"] for e in events}
    assert tracked_pids == {100, 300}

    # Verify PID 300's peak was updated
    row = conn.execute(
        "SELECT peak_score FROM process_events WHERE pid = 300 AND exit_time IS NULL"
    ).fetchone()
    assert row[0] == 80

    # PID 100 disappears, PID 300 stays
    tracker.update(
        [
            make_score(pid=300, command="proc_c", score=75, captured_at=1706000300.0),
        ]
    )

    events = get_open_events(conn, 1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 300

    conn.close()
