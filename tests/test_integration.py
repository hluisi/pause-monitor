"""End-to-end integration tests for the Ring Buffer Sentinel system."""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from pause_monitor.config import Config, SentinelConfig, TiersConfig
from pause_monitor.ringbuffer import (
    BufferContents,
    ProcessInfo,
    ProcessSnapshot,
    RingBuffer,
    RingSample,
)
from pause_monitor.sentinel import Tier, TierManager
from pause_monitor.storage import Event, get_events, init_database, insert_event
from pause_monitor.stress import StressBreakdown


def make_test_config(tmp_path: Path) -> Config:
    """Create a Config with paths pointing to tmp_path."""
    config = Config()
    config.sentinel = SentinelConfig(
        fast_interval_ms=10,
        slow_interval_ms=100,
        ring_buffer_seconds=1,
    )
    config.tiers = TiersConfig(
        elevated_threshold=15,
        critical_threshold=50,
    )
    # Patch the properties to use tmp_path
    type(config).data_dir = PropertyMock(return_value=tmp_path)
    type(config).events_dir = PropertyMock(return_value=tmp_path / "events")
    type(config).db_path = PropertyMock(return_value=tmp_path / "data.db")
    return config


class TestTierManager:
    """Test tier state machine transitions."""

    def test_tier_escalation_to_elevated(self):
        """TierManager transitions from SENTINEL to ELEVATED on high stress."""
        manager = TierManager(elevated_threshold=15, critical_threshold=50)
        assert manager.current_tier == Tier.SENTINEL

        # Update with stress above elevated threshold
        action = manager.update(stress_total=30)
        assert action == "tier2_entry"
        assert manager.current_tier == Tier.ELEVATED

    def test_tier_escalation_to_critical(self):
        """TierManager transitions to CRITICAL on very high stress."""
        manager = TierManager(elevated_threshold=15, critical_threshold=50)

        # First go to elevated
        manager.update(stress_total=30)
        assert manager.current_tier == Tier.ELEVATED

        # Then to critical
        action = manager.update(stress_total=60)
        assert action == "tier3_entry"
        assert manager.current_tier == Tier.CRITICAL

    def test_tier_tracks_peak_stress(self):
        """TierManager tracks peak stress during elevated states."""
        manager = TierManager(elevated_threshold=15, critical_threshold=50)

        # Enter elevated with stress=30
        manager.update(stress_total=30)
        assert manager.peak_stress == 30

        # New peak at 40
        action = manager.update(stress_total=40)
        assert action == "tier2_peak"
        assert manager.peak_stress == 40

        # Lower stress doesn't reset peak
        manager.update(stress_total=35)
        assert manager.peak_stress == 40


class TestRingBufferDataFlow:
    """Test data flows correctly through the ring buffer."""

    def test_buffer_captures_samples_across_tiers(self):
        """Ring buffer correctly stores samples from different tiers."""
        buffer = RingBuffer(max_samples=100)

        # Add samples from different tiers using push(stress, tier)
        for tier in [1, 2, 2, 3, 3, 3]:
            stress = StressBreakdown(
                load=tier * 10, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0
            )
            buffer.push(stress, tier)

        contents = buffer.freeze()
        assert len(contents.samples) == 6

        # Verify tier progression in samples
        tiers = [s.tier for s in contents.samples]
        assert tiers == [1, 2, 2, 3, 3, 3]

    def test_buffer_captures_process_snapshots(self):
        """Ring buffer stores process snapshots via snapshot_processes."""
        buffer = RingBuffer(max_samples=100)

        # Capture process snapshot (uses real psutil)
        buffer.snapshot_processes(trigger="tier2_entry")

        contents = buffer.freeze()
        assert len(contents.snapshots) == 1
        assert contents.snapshots[0].trigger == "tier2_entry"
        # Should have captured some processes (at least the test runner)
        assert len(contents.snapshots[0].by_cpu) > 0
        assert len(contents.snapshots[0].by_memory) > 0

    def test_buffer_evicts_old_samples(self):
        """Ring buffer evicts old samples when full."""
        buffer = RingBuffer(max_samples=5)

        # Add more samples than buffer can hold
        for i in range(10):
            stress = StressBreakdown(load=i, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
            buffer.push(stress, tier=1)

        contents = buffer.freeze()
        assert len(contents.samples) == 5

        # Should have the last 5 samples (loads 5-9)
        loads = [s.stress.load for s in contents.samples]
        assert loads == [5, 6, 7, 8, 9]


class TestCulpritIdentification:
    """Test culprit identification from buffer contents."""

    def test_high_memory_identifies_memory_culprits(self):
        """High memory stress identifies top memory consumers."""
        from pause_monitor.forensics import identify_culprits

        # Create buffer with high memory stress
        stress = StressBreakdown(load=5, memory=25, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
        samples = [RingSample(timestamp=datetime.now(), stress=stress, tier=2) for _ in range(3)]

        snapshots = [
            ProcessSnapshot(
                timestamp=datetime.now(),
                trigger="tier2_entry",
                by_cpu=[ProcessInfo(pid=1, name="cpu_hog", cpu_pct=100, memory_mb=500)],
                by_memory=[ProcessInfo(pid=2, name="mem_hog", cpu_pct=10, memory_mb=8000)],
            )
        ]

        contents = BufferContents(samples=samples, snapshots=snapshots)
        culprits = identify_culprits(contents)

        # Should identify memory as the primary factor
        assert len(culprits) == 1
        assert culprits[0]["factor"] == "memory"
        assert "mem_hog" in culprits[0]["processes"]

    def test_multiple_factors_ranked_by_score(self):
        """Multiple elevated factors are ranked by score."""
        from pause_monitor.forensics import identify_culprits

        stress = StressBreakdown(load=30, memory=20, thermal=0, latency=0, io=15, gpu=0, wakeups=0)
        samples = [RingSample(timestamp=datetime.now(), stress=stress, tier=3) for _ in range(3)]

        snapshots = [
            ProcessSnapshot(
                timestamp=datetime.now(),
                trigger="tier3_entry",
                by_cpu=[ProcessInfo(pid=1, name="worker", cpu_pct=200, memory_mb=1000)],
                by_memory=[ProcessInfo(pid=1, name="worker", cpu_pct=200, memory_mb=1000)],
            )
        ]

        contents = BufferContents(samples=samples, snapshots=snapshots)
        culprits = identify_culprits(contents)

        # Should have multiple factors, sorted by score
        assert len(culprits) == 3  # load, memory, io all above threshold
        assert culprits[0]["factor"] == "load"  # Highest score (30)
        assert culprits[1]["factor"] == "memory"  # Second (20)
        assert culprits[2]["factor"] == "io"  # Third (15)


class TestEventStatusIntegration:
    """Test event status management across the system."""

    @pytest.fixture
    def db_with_events(self, tmp_path) -> Path:
        """Create database with test events."""
        db_path = tmp_path / "test.db"
        init_database(db_path)

        conn = sqlite3.connect(db_path)
        stress = StressBreakdown(load=50, memory=20, thermal=0, latency=0, io=0, gpu=10, wakeups=5)

        # Insert events with different statuses
        for status in ["unreviewed", "reviewed", "pinned", "dismissed"]:
            event = Event(
                timestamp=datetime.now(),
                duration=2.5,
                stress=stress,
                culprits=["test_process"],
                event_dir=None,
                status=status,
            )
            insert_event(conn, event)

        conn.close()
        return db_path

    def test_get_events_filters_by_status(self, db_with_events):
        """get_events correctly filters by status."""
        conn = sqlite3.connect(db_with_events)

        # Get only unreviewed events
        unreviewed = get_events(conn, status="unreviewed")
        assert len(unreviewed) == 1
        assert unreviewed[0].status == "unreviewed"

        # Get only pinned events
        pinned = get_events(conn, status="pinned")
        assert len(pinned) == 1
        assert pinned[0].status == "pinned"

        conn.close()

    def test_events_include_gpu_and_wakeups(self, db_with_events):
        """Events correctly store and retrieve GPU and wakeups stress."""
        conn = sqlite3.connect(db_with_events)
        events = get_events(conn, limit=1)
        conn.close()

        assert events[0].stress.gpu == 10
        assert events[0].stress.wakeups == 5


class TestDaemonSentinelIntegration:
    """Test daemon integration with Sentinel."""

    @pytest.fixture
    def mock_config(self, tmp_path) -> Config:
        """Create config pointing to temp directory."""
        return make_test_config(tmp_path)

    def test_daemon_initializes_with_sentinel(self, mock_config):
        """Daemon correctly initializes Sentinel with callbacks."""
        from pause_monitor.daemon import Daemon

        daemon = Daemon(mock_config)

        # Verify Sentinel is initialized
        assert daemon.sentinel is not None
        assert daemon.ring_buffer is not None
        # Callbacks should be wired
        assert daemon.sentinel.on_tier_change is not None
        assert daemon.sentinel.on_pause_detected is not None

    @pytest.mark.asyncio
    async def test_daemon_handles_tier_change(self, mock_config):
        """Daemon responds to tier change callbacks."""
        from pause_monitor.daemon import Daemon

        daemon = Daemon(mock_config)
        daemon.notifier = MagicMock()
        daemon.notifier.elevated_entered = MagicMock()
        daemon.notifier.critical_stress = MagicMock()

        # Simulate tier 2 entry
        await daemon._handle_tier_change(action="tier2_entry", tier=2)

        # Should update state (elevated_since is set to datetime when elevated)
        assert daemon.state.elevated_since is not None
        daemon.notifier.elevated_entered.assert_called_once()

    @pytest.mark.asyncio
    async def test_daemon_handles_critical_tier(self, mock_config):
        """Daemon responds to tier 3 entry callbacks."""
        from pause_monitor.daemon import Daemon

        daemon = Daemon(mock_config)
        daemon.notifier = MagicMock()
        daemon.notifier.elevated_entered = MagicMock()
        daemon.notifier.critical_stress = MagicMock()

        # Simulate tier 3 entry
        await daemon._handle_tier_change(action="tier3_entry", tier=3)

        # Should update both elevated and critical state
        # (elevated_since and critical_since are set to datetime when in that state)
        assert daemon.state.elevated_since is not None
        assert daemon.state.critical_since is not None
        daemon.notifier.critical_stress.assert_called_once()

    @pytest.mark.asyncio
    async def test_daemon_records_pause_event(self, mock_config, tmp_path):
        """Daemon records pause events with ring buffer data."""
        from pause_monitor.daemon import Daemon

        # Ensure events directory exists
        (tmp_path / "events").mkdir(parents=True, exist_ok=True)
        init_database(mock_config.db_path)

        daemon = Daemon(mock_config)
        daemon._conn = sqlite3.connect(mock_config.db_path)
        daemon.notifier = MagicMock()
        daemon.notifier.pause_detected = MagicMock()

        # Create buffer contents
        stress = StressBreakdown(load=40, memory=30, thermal=0, latency=0, io=0, gpu=15, wakeups=0)
        samples = [RingSample(timestamp=datetime.now(), stress=stress, tier=3) for _ in range(5)]
        snapshots = [
            ProcessSnapshot(
                timestamp=datetime.now(),
                trigger="pause",
                by_cpu=[ProcessInfo(pid=1, name="culprit", cpu_pct=300, memory_mb=2000)],
                by_memory=[ProcessInfo(pid=1, name="culprit", cpu_pct=300, memory_mb=2000)],
            )
        ]
        contents = BufferContents(samples=samples, snapshots=snapshots)

        # Handle pause
        await daemon._handle_pause_from_sentinel(
            actual=5.0,
            expected=0.1,
            contents=contents,
        )

        # Verify event was recorded
        events = get_events(daemon._conn)
        assert len(events) == 1
        assert events[0].duration == 5.0
        assert events[0].stress.load == 40
        assert events[0].stress.gpu == 15
        # Should have identified culprits from high load/memory
        assert len(events[0].culprits) > 0

        daemon._conn.close()
