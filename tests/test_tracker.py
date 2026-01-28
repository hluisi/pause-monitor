# tests/test_tracker.py
"""Tests for per-process band tracker."""

import json


def test_tracker_creates_event_on_threshold_crossing(tmp_path):
    """ProcessTracker creates event when score crosses tracking threshold."""
    from pause_monitor.collector import ProcessScore
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()  # tracking_band="elevated", threshold=40
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Score below threshold — no event
    score_low = ProcessScore(
        pid=123,
        command="test",
        cpu=10.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=30,
        categories=frozenset(),
        captured_at=1706000100.0,
    )
    tracker.update([score_low])
    assert len(get_open_events(conn, 1706000000)) == 0

    # Score above threshold — event created
    score_high = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=50,
        categories=frozenset(["cpu"]),
        captured_at=1706000101.0,
    )
    tracker.update([score_high])
    events = get_open_events(conn, 1706000000)
    assert len(events) == 1
    assert events[0]["pid"] == 123

    conn.close()


def test_tracker_closes_event_when_score_drops(tmp_path):
    """ProcessTracker closes event when score drops below threshold."""
    from pause_monitor.collector import ProcessScore
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    score_high = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=50,
        categories=frozenset(["cpu"]),
        captured_at=1706000100.0,
    )
    tracker.update([score_high])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Exit bad state
    score_low = ProcessScore(
        pid=123,
        command="test",
        cpu=10.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=30,
        categories=frozenset(),
        captured_at=1706000200.0,
    )
    tracker.update([score_low])
    assert len(get_open_events(conn, 1706000000)) == 0

    conn.close()


def test_tracker_updates_peak(tmp_path):
    """ProcessTracker updates peak when score increases."""
    from pause_monitor.collector import ProcessScore
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter at 50
    score1 = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=50,
        categories=frozenset(["cpu"]),
        captured_at=1706000100.0,
    )
    tracker.update([score1])

    # Peak at 80
    score2 = ProcessScore(
        pid=123,
        command="test",
        cpu=80.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=80,
        categories=frozenset(["cpu"]),
        captured_at=1706000101.0,
    )
    tracker.update([score2])

    row = conn.execute(
        "SELECT peak_score, peak_band FROM process_events WHERE pid = 123"
    ).fetchone()
    assert row[0] == 80
    assert row[1] == "critical"

    conn.close()


def test_tracker_closes_missing_pids(tmp_path):
    """ProcessTracker closes events for PIDs no longer in scores."""
    from pause_monitor.collector import ProcessScore
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, get_open_events, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # PID 123 enters bad state
    score = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=50,
        categories=frozenset(["cpu"]),
        captured_at=1706000100.0,
    )
    tracker.update([score])
    assert len(get_open_events(conn, 1706000000)) == 1

    # PID 123 disappears from scores (process ended or no longer selected)
    tracker.update([])
    assert len(get_open_events(conn, 1706000000)) == 0

    conn.close()


def test_tracker_restores_state_from_db(tmp_path):
    """ProcessTracker restores tracking state from open events on init."""
    from pause_monitor.collector import ProcessScore
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
    score = ProcessScore(
        pid=456,
        command="preexisting",
        cpu=85.0,
        state="running",
        mem=1000,
        cmprs=0,
        pageins=0,
        csw=10,
        sysbsd=5,
        threads=2,
        score=85,
        categories=frozenset(["cpu"]),
        captured_at=1706000200.0,
    )
    tracker.update([score])

    row = conn.execute(
        "SELECT peak_score, peak_band FROM process_events WHERE pid = 456"
    ).fetchone()
    assert row[0] == 85
    assert row[1] == "critical"

    conn.close()


def test_tracker_inserts_entry_snapshot(tmp_path):
    """ProcessTracker inserts entry snapshot when event opens."""
    from pause_monitor.collector import ProcessScore
    from pause_monitor.config import BandsConfig
    from pause_monitor.storage import get_connection, init_database
    from pause_monitor.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    score = ProcessScore(
        pid=789,
        command="snap_test",
        cpu=60.0,
        state="running",
        mem=2000,
        cmprs=100,
        pageins=50,
        csw=20,
        sysbsd=10,
        threads=4,
        score=55,
        categories=frozenset(["cpu", "mem"]),
        captured_at=1706000100.0,
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
