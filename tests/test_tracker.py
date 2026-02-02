# tests/test_tracker.py
"""Tests for per-process band tracker."""

from rogue_hunter.collector import ProcessScore


def _get_band_for_score(score: int) -> str:
    """Derive band name from score using default BandsConfig thresholds."""
    # Default thresholds: medium=20, elevated=40, high=60, critical=80
    if score >= 80:
        return "critical"
    elif score >= 60:
        return "high"
    elif score >= 40:
        return "elevated"
    elif score >= 20:
        return "medium"
    else:
        return "low"


def make_score(
    pid: int = 123,
    command: str = "test",
    score: int = 50,
    captured_at: float = 1706000100.0,
    state: str = "running",
    band: str | None = None,
    **kwargs,
) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    # Derive band from score if not explicitly provided
    if band is None:
        band = _get_band_for_score(score)
    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=captured_at,
        cpu=kwargs.get("cpu", 50.0),
        mem=kwargs.get("mem", 1000),
        mem_peak=kwargs.get("mem_peak", 1500),
        pageins=kwargs.get("pageins", 0),
        pageins_rate=kwargs.get("pageins_rate", 0.0),
        faults=kwargs.get("faults", 0),
        faults_rate=kwargs.get("faults_rate", 0.0),
        disk_io=kwargs.get("disk_io", 0),
        disk_io_rate=kwargs.get("disk_io_rate", 0.0),
        csw=kwargs.get("csw", 10),
        csw_rate=kwargs.get("csw_rate", 0.0),
        syscalls=kwargs.get("syscalls", 5),
        syscalls_rate=kwargs.get("syscalls_rate", 0.0),
        threads=kwargs.get("threads", 2),
        mach_msgs=kwargs.get("mach_msgs", 0),
        mach_msgs_rate=kwargs.get("mach_msgs_rate", 0.0),
        instructions=kwargs.get("instructions", 0),
        cycles=kwargs.get("cycles", 0),
        ipc=kwargs.get("ipc", 0.0),
        energy=kwargs.get("energy", 0),
        energy_rate=kwargs.get("energy_rate", 0.0),
        wakeups=kwargs.get("wakeups", 0),
        wakeups_rate=kwargs.get("wakeups_rate", 0.0),
        runnable_time=kwargs.get("runnable_time", 0),
        runnable_time_rate=kwargs.get("runnable_time_rate", 0.0),
        qos_interactive=kwargs.get("qos_interactive", 0),
        qos_interactive_rate=kwargs.get("qos_interactive_rate", 0.0),
        gpu_time=kwargs.get("gpu_time", 0),
        gpu_time_rate=kwargs.get("gpu_time_rate", 0.0),
        zombie_children=kwargs.get("zombie_children", 0),
        state=state,
        priority=kwargs.get("priority", 31),
        score=score,
        band=band,
        blocking_score=kwargs.get("blocking_score", score * 0.4),
        contention_score=kwargs.get("contention_score", score * 0.3),
        pressure_score=kwargs.get("pressure_score", score * 0.2),
        efficiency_score=kwargs.get("efficiency_score", score * 0.1),
        dominant_category=kwargs.get("dominant_category", "blocking"),
        dominant_metrics=kwargs.get("dominant_metrics", ["cpu:50%"] if score >= 40 else []),
    )


def test_tracker_creates_event_on_threshold_crossing(tmp_path):
    """ProcessTracker creates event when score crosses tracking threshold."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_open_events, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_open_events, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_open_events, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import (
        create_process_event,
        get_connection,
        init_database,
        insert_process_snapshot,
        update_process_event_peak,
    )
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Pre-create an open event in DB with a snapshot
    event_id = create_process_event(
        conn,
        pid=456,
        command="preexisting",
        boot_time=1706000000,
        entry_time=1706000050.0,
        entry_band="elevated",
        peak_score=60,
        peak_band="high",
    )

    # Insert entry snapshot and set as peak
    entry_score = make_score(pid=456, command="preexisting", score=60, captured_at=1706000050.0)
    snapshot_id = insert_process_snapshot(conn, event_id, "entry", entry_score)
    update_process_event_peak(conn, event_id, 60, "high", snapshot_id)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Tracker should have loaded the open event
    assert 456 in tracker.tracked
    assert tracker.tracked[456].peak_score == 60
    assert tracker.tracked[456].peak_snapshot_id == snapshot_id

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_process_snapshots, init_database
    from rogue_hunter.tracker import ProcessTracker

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
        pageins=50,
        csw=20,
        syscalls=10,
        threads=4,
        dominant_category="blocking",
        dominant_metrics=["cpu:60%", "mem:2KB"],
    )
    tracker.update([score])

    # Get the event ID
    event_id = tracker.tracked[789].event_id

    # Check snapshot was inserted
    snapshots = get_process_snapshots(conn, event_id)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap["snapshot_type"] == "entry"
    assert snap["score"] == 55
    assert snap["cpu"] == 60.0
    assert snap["mem"] == 2000
    assert snap["dominant_category"] == "blocking"
    assert snap["dominant_metrics"] == ["cpu:60%", "mem:2KB"]

    conn.close()


def test_tracker_inserts_exit_snapshot_on_score_drop(tmp_path):
    """ProcessTracker inserts exit snapshot when score drops below threshold."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_process_snapshots, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    snapshots = get_process_snapshots(conn, event_id)
    assert len(snapshots) == 2
    types = {s["snapshot_type"] for s in snapshots}
    assert types == {"entry", "exit"}

    # Verify exit snapshot has the low score
    exit_snap = [s for s in snapshots if s["snapshot_type"] == "exit"][0]
    assert exit_snap["score"] == 30

    conn.close()


def test_tracker_no_exit_snapshot_for_disappeared_pid(tmp_path):
    """ProcessTracker does NOT insert exit snapshot when PID disappears."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

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
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_snapshot, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter at 50
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])

    # Get initial peak_snapshot_id
    initial_snapshot_id = conn.execute(
        "SELECT peak_snapshot_id FROM process_events WHERE pid = 123"
    ).fetchone()[0]

    # Update with same score (different timestamp)
    tracker.update([make_score(pid=123, score=50, captured_at=1706000200.0)])

    # Peak snapshot_id should be unchanged (same snapshot, not updated)
    new_snapshot_id = conn.execute(
        "SELECT peak_snapshot_id FROM process_events WHERE pid = 123"
    ).fetchone()[0]
    assert initial_snapshot_id == new_snapshot_id

    # Verify timestamp in snapshot is still the original
    snapshot = get_snapshot(conn, new_snapshot_id)
    assert snapshot["captured_at"] == 1706000100.0

    conn.close()


def test_tracker_handles_multiple_simultaneous_processes(tmp_path):
    """ProcessTracker tracks multiple PIDs simultaneously."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_open_events, init_database
    from rogue_hunter.tracker import ProcessTracker

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


def test_tracker_inserts_checkpoint_snapshots(tmp_path):
    """ProcessTracker inserts periodic checkpoint snapshots while tracking.

    Note: Peak updates also insert checkpoint snapshots (since the peak snapshot
    is stored by ID), so we use scores that don't exceed the entry peak to
    isolate the periodic checkpoint behavior.
    """
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, SNAPSHOT_ENTRY, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Use short checkpoint interval for testing
    bands = BandsConfig(checkpoint_interval=10)  # 10 seconds
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state at t=100 with score=50
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Update at t=105 with same score (no peak update, no checkpoint yet)
    tracker.update([make_score(pid=123, score=50, captured_at=1706000105.0)])

    # Check only entry snapshot exists (no checkpoint yet - only 5 seconds)
    rows = conn.execute(
        "SELECT snapshot_type FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    types = [row[0] for row in rows]
    assert types == [SNAPSHOT_ENTRY]

    # Update at t=115 with same score (15 seconds since entry - checkpoint triggers)
    tracker.update([make_score(pid=123, score=50, captured_at=1706000115.0)])

    # Now we should have entry + 1 checkpoint
    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 1

    # Another update at t=130 (25 seconds since last checkpoint - another checkpoint)
    tracker.update([make_score(pid=123, score=50, captured_at=1706000130.0)])

    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 2

    conn.close()
