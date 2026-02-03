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
    cpu = kwargs.get("cpu", 50.0)
    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=captured_at,
        cpu=cpu,
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
        cpu_share=kwargs.get("cpu_share", cpu / 100.0),
        gpu_share=kwargs.get("gpu_share", 0.0),
        mem_share=kwargs.get("mem_share", 0.0),
        disk_share=kwargs.get("disk_share", 0.0),
        wakeups_share=kwargs.get("wakeups_share", 0.0),
        disproportionality=kwargs.get("disproportionality", cpu / 100.0),
        dominant_resource=kwargs.get("dominant_resource", "cpu"),
    )


def test_tracker_creates_event_on_threshold_crossing(tmp_path):
    """ProcessTracker creates event when score crosses tracking threshold."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_open_events, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig(tracking_band="elevated")  # tracking_band="elevated", threshold=40
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

    # Use  for simple test (closes on first sample below threshold)
    bands = BandsConfig(tracking_band="elevated")
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Exit bad state
    tracker.update([make_score(pid=123, score=30, captured_at=1706000200.0)])
    assert len(get_open_events(conn, 1706000000)) == 0

    conn.close()


def test_tracker_writes_exit_snapshot_on_score_drop(tmp_path):
    """ProcessTracker writes exit snapshot with final metrics when score drops below threshold."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import (
        get_connection,
        get_open_events,
        get_process_events,
        get_process_snapshots,
        init_database,
    )
    from rogue_hunter.tracker import SNAPSHOT_ENTRY, SNAPSHOT_EXIT, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    #  means closes on first sample below threshold
    bands = BandsConfig(tracking_band="elevated")
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state with high CPU
    tracker.update([make_score(pid=123, score=50, cpu=80.0, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Get event_id before it closes
    events = get_process_events(conn, boot_time=1706000000)
    event_id = events[0]["id"]

    # Exit bad state with lower CPU
    tracker.update([make_score(pid=123, score=30, cpu=15.0, captured_at=1706000200.0)])
    assert len(get_open_events(conn, 1706000000)) == 0

    # Verify snapshots: should have entry + exit
    snapshots = get_process_snapshots(conn, event_id)
    assert len(snapshots) == 2

    entry_snap = next(s for s in snapshots if s["snapshot_type"] == SNAPSHOT_ENTRY)
    exit_snap = next(s for s in snapshots if s["snapshot_type"] == SNAPSHOT_EXIT)

    # Entry snapshot has high score/cpu from when we entered
    assert entry_snap["score"] == 50
    assert entry_snap["cpu"] == 80.0

    # Exit snapshot has dropped score/cpu from when we exited
    assert exit_snap["score"] == 30
    assert exit_snap["cpu"] == 15.0
    assert exit_snap["captured_at"] == 1706000200.0

    conn.close()


def test_tracker_no_exit_snapshot_when_pid_disappears(tmp_path):
    """ProcessTracker closes event without exit snapshot when PID is no longer present."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import (
        get_connection,
        get_open_events,
        get_process_events,
        get_process_snapshots,
        init_database,
    )
    from rogue_hunter.tracker import SNAPSHOT_ENTRY, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig(tracking_band="elevated")
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter bad state
    tracker.update([make_score(pid=123, score=50, captured_at=1706000100.0)])
    assert len(get_open_events(conn, 1706000000)) == 1

    # Get event_id before it closes
    events = get_process_events(conn, boot_time=1706000000)
    event_id = events[0]["id"]

    # PID disappears entirely (not in update list)
    tracker.update([make_score(pid=999, score=50, captured_at=1706000200.0)])
    assert len(get_open_events(conn, 1706000000)) == 1  # 999 is now tracked
    assert 123 not in tracker.tracked  # 123 was closed

    # Verify snapshots: only entry, no exit (we don't have exit data)
    snapshots = get_process_snapshots(conn, event_id)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_type"] == SNAPSHOT_ENTRY

    conn.close()


def test_tracker_updates_peak(tmp_path):
    """ProcessTracker updates peak when score increases."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig(tracking_band="elevated")
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

    bands = BandsConfig(tracking_band="elevated")
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

    bands = BandsConfig(tracking_band="elevated")
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

    bands = BandsConfig(tracking_band="elevated")
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
    assert snap["dominant_resource"] == "cpu"
    assert isinstance(snap["disproportionality"], float)

    conn.close()


def test_tracker_inserts_exit_snapshot_on_score_drop(tmp_path):
    """ProcessTracker inserts exit snapshot when score drops below threshold."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, get_process_snapshots, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Use  for simple test
    bands = BandsConfig(tracking_band="elevated")
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

    bands = BandsConfig(tracking_band="elevated")
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

    bands = BandsConfig(tracking_band="elevated")
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

    # Use  for simple test
    bands = BandsConfig(tracking_band="elevated")
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
    """ProcessTracker inserts checkpoint snapshots based on sample count.

    Note: Peak updates also insert checkpoint snapshots (since the peak snapshot
    is stored by ID), so we use scores that don't exceed the entry peak to
    isolate the periodic checkpoint behavior.

    Uses sample-based checkpointing: elevated_checkpoint_samples=3.
    """
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, SNAPSHOT_ENTRY, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Use elevated_checkpoint_samples=3 for testing
    bands = BandsConfig(elevated_checkpoint_samples=3)
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter elevated band with score=50
    tracker.update([make_score(pid=123, score=50, band="elevated", captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Sample 2: No checkpoint yet (only 1 sample since entry)
    tracker.update([make_score(pid=123, score=50, band="elevated", captured_at=1706000105.0)])

    # Check only entry snapshot exists (no checkpoint yet)
    rows = conn.execute(
        "SELECT snapshot_type FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    types = [row[0] for row in rows]
    assert types == [SNAPSHOT_ENTRY]

    # Sample 3: Still no checkpoint (only 2 samples since entry)
    tracker.update([make_score(pid=123, score=50, band="elevated", captured_at=1706000110.0)])

    # Sample 4: Checkpoint triggers (3 samples since entry)
    tracker.update([make_score(pid=123, score=50, band="elevated", captured_at=1706000115.0)])

    # Now we should have entry + 1 checkpoint
    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 1

    # Samples 5-7: After 3 more samples, another checkpoint
    for i in range(3):
        score = make_score(pid=123, score=50, band="elevated", captured_at=1706000120.0 + i)
        tracker.update([score])

    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 2

    conn.close()


# --- Graduated Capture Frequency Tests ---


def test_low_band_not_tracked(tmp_path):
    """Processes in low band are not tracked."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()  # Default tracking_band="medium", threshold=20
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Low band process (score=15, band="low") should not be tracked
    tracker.update([make_score(pid=123, score=15, band="low", captured_at=1706000100.0)])

    assert 123 not in tracker.tracked
    assert len(tracker.tracked) == 0

    conn.close()


def test_medium_band_checkpoints_every_n_samples(tmp_path):
    """Medium band checkpoints every N samples.

    With medium_checkpoint_samples=3:
    - Sample 1: Creates event + entry snapshot, samples_since_checkpoint=0
    - Sample 2: No checkpoint, samples_since_checkpoint=1
    - Sample 3: No checkpoint, samples_since_checkpoint=2
    - Sample 4: Checkpoint (3 samples since last), samples_since_checkpoint=0 (reset)
    """
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, SNAPSHOT_ENTRY, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Use medium_checkpoint_samples=3 for testing
    bands = BandsConfig(medium_checkpoint_samples=3)
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Sample 1: Enter medium band - creates entry snapshot
    # Use score=45 (above medium threshold of 40)
    tracker.update([make_score(pid=123, score=45, band="medium", captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Verify entry snapshot only
    rows = conn.execute(
        "SELECT snapshot_type FROM process_snapshots WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    assert [r[0] for r in rows] == [SNAPSHOT_ENTRY]
    assert tracker.tracked[123].samples_since_checkpoint == 0

    # Sample 2: No checkpoint yet
    tracker.update([make_score(pid=123, score=45, band="medium", captured_at=1706000101.0)])
    assert tracker.tracked[123].samples_since_checkpoint == 1

    # Sample 3: Still no checkpoint
    tracker.update([make_score(pid=123, score=45, band="medium", captured_at=1706000102.0)])
    assert tracker.tracked[123].samples_since_checkpoint == 2

    # Sample 4: Checkpoint triggers (3 samples since entry)
    tracker.update([make_score(pid=123, score=45, band="medium", captured_at=1706000103.0)])
    assert tracker.tracked[123].samples_since_checkpoint == 0  # Reset after checkpoint

    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 1

    conn.close()


def test_elevated_band_checkpoints_more_frequently(tmp_path):
    """Elevated band checkpoints more frequently than medium.

    With medium_checkpoint_samples=20 and elevated_checkpoint_samples=10:
    - After 10 samples, elevated should checkpoint but medium should not
    """
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig(medium_checkpoint_samples=20, elevated_checkpoint_samples=10)
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Start tracking both processes
    # Use score=45 for medium (above threshold of 40), score=55 for elevated (above threshold of 50)
    tracker.update(
        [
            make_score(pid=100, score=45, band="medium", captured_at=1706000100.0),
            make_score(pid=200, score=50, band="elevated", captured_at=1706000100.0),
        ]
    )
    medium_event_id = tracker.tracked[100].event_id
    elevated_event_id = tracker.tracked[200].event_id

    # Send 10 more samples (total 11 including entry)
    for i in range(1, 11):
        tracker.update(
            [
                make_score(pid=100, score=45, band="medium", captured_at=1706000100.0 + i),
                make_score(pid=200, score=50, band="elevated", captured_at=1706000100.0 + i),
            ]
        )

    # Elevated should have checkpoint (10 samples reached)
    # samples_since_checkpoint should be 0 (just reset after checkpoint)
    assert tracker.tracked[200].samples_since_checkpoint == 0

    # Medium should NOT have checkpoint yet (only 10 samples, need 20)
    # samples_since_checkpoint should be 10
    assert tracker.tracked[100].samples_since_checkpoint == 10

    # Verify checkpoint counts
    elevated_checkpoints = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (elevated_event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert elevated_checkpoints == 1

    medium_checkpoints = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (medium_event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert medium_checkpoints == 0

    conn.close()


def test_high_band_checkpoints_every_sample(tmp_path):
    """High band checkpoints every sample."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter high band (score >= 50)
    tracker.update([make_score(pid=123, score=55, band="high", captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Each subsequent update should trigger a checkpoint
    for i in range(1, 4):
        tracker.update([make_score(pid=123, score=55, band="high", captured_at=1706000100.0 + i)])

    # Should have 3 checkpoints (one per sample after entry)
    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 3

    # samples_since_checkpoint should always reset to 0 for high band
    assert tracker.tracked[123].samples_since_checkpoint == 0

    conn.close()


def test_critical_band_checkpoints_every_sample(tmp_path):
    """Critical band checkpoints every sample."""
    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import SNAPSHOT_CHECKPOINT, ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    bands = BandsConfig()
    tracker = ProcessTracker(conn, bands, boot_time=1706000000)

    # Enter critical band (score >= 70)
    tracker.update([make_score(pid=123, score=75, band="critical", captured_at=1706000100.0)])
    event_id = tracker.tracked[123].event_id

    # Each subsequent update should trigger a checkpoint
    for i in range(1, 4):
        score = make_score(pid=123, score=75, band="critical", captured_at=1706000100.0 + i)
        tracker.update([score])

    # Should have 3 checkpoints (one per sample after entry)
    checkpoint_count = conn.execute(
        "SELECT COUNT(*) FROM process_snapshots WHERE event_id = ? AND snapshot_type = ?",
        (event_id, SNAPSHOT_CHECKPOINT),
    ).fetchone()[0]
    assert checkpoint_count == 3

    # samples_since_checkpoint should always reset to 0 for critical band
    assert tracker.tracked[123].samples_since_checkpoint == 0

    conn.close()


async def test_forensics_only_at_configured_band(tmp_path):
    """Forensics triggers only at forensics_band from config."""
    import asyncio

    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Track forensics trigger calls
    forensics_calls = []

    async def on_forensics_trigger(event_id: int, reason: str) -> None:
        forensics_calls.append((event_id, reason))

    # Explicitly set forensics_band="critical" for this test
    bands = BandsConfig(forensics_band="critical")
    tracker = ProcessTracker(
        conn, bands, boot_time=1706000000, on_forensics_trigger=on_forensics_trigger
    )

    # High band (score=55, threshold=50) should NOT trigger forensics when forensics_band=critical
    tracker.update([make_score(pid=123, score=55, band="high", captured_at=1706000100.0)])
    await asyncio.sleep(0)  # Let tasks run
    assert len(forensics_calls) == 0, "High band shouldn't trigger (forensics=critical)"

    # Critical band (score=75, threshold=70) SHOULD trigger forensics
    tracker.update([make_score(pid=456, score=75, band="critical", captured_at=1706000101.0)])
    await asyncio.sleep(0)  # Let tasks run
    assert len(forensics_calls) == 1, "Critical band should trigger forensics"
    assert "band_entry_critical" in forensics_calls[0][1]

    conn.close()


async def test_forensics_on_escalation_to_configured_band(tmp_path):
    """Forensics triggers when escalating INTO forensics_band."""
    import asyncio

    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Track forensics trigger calls
    forensics_calls = []

    async def on_forensics_trigger(event_id: int, reason: str) -> None:
        forensics_calls.append((event_id, reason))

    # Explicitly set forensics_band="critical" for this test
    bands = BandsConfig(forensics_band="critical")
    tracker = ProcessTracker(
        conn, bands, boot_time=1706000000, on_forensics_trigger=on_forensics_trigger
    )

    # Start at high band (score=55) - no forensics when forensics_band=critical
    tracker.update([make_score(pid=123, score=55, band="high", captured_at=1706000100.0)])
    await asyncio.sleep(0)
    assert len(forensics_calls) == 0, "High band shouldn't trigger (forensics=critical)"

    # Escalate to critical (score=75) - forensics triggered
    tracker.update([make_score(pid=123, score=75, band="critical", captured_at=1706000101.0)])
    await asyncio.sleep(0)
    assert len(forensics_calls) == 1, "Escalation to critical should trigger forensics"
    assert "peak_escalation_critical" in forensics_calls[0][1]

    conn.close()


async def test_forensics_configurable_to_high(tmp_path):
    """Forensics can be configured to trigger at high band."""
    import asyncio

    from rogue_hunter.config import BandsConfig
    from rogue_hunter.storage import get_connection, init_database
    from rogue_hunter.tracker import ProcessTracker

    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = get_connection(db_path)

    # Track forensics trigger calls
    forensics_calls = []

    async def on_forensics_trigger(event_id: int, reason: str) -> None:
        forensics_calls.append((event_id, reason))

    # Configure forensics_band="high" (threshold=50)
    bands = BandsConfig(forensics_band="high")
    tracker = ProcessTracker(
        conn, bands, boot_time=1706000000, on_forensics_trigger=on_forensics_trigger
    )

    # High band (score=55) SHOULD now trigger forensics
    tracker.update([make_score(pid=123, score=55, band="high", captured_at=1706000100.0)])
    await asyncio.sleep(0)
    assert len(forensics_calls) == 1, "High band should trigger forensics with forensics_band=high"
    assert "band_entry_high" in forensics_calls[0][1]

    conn.close()
