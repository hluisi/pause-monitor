"""End-to-end integration tests for the Ring Buffer and Tier system."""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from pause_monitor.collector import PowermetricsResult
from pause_monitor.config import Config, SentinelConfig, TiersConfig
from pause_monitor.ringbuffer import (
    BufferContents,
    ProcessInfo,
    ProcessSnapshot,
    RingBuffer,
    RingSample,
)
from pause_monitor.sentinel import Tier, TierManager
from pause_monitor.storage import create_event, finalize_event, get_events, init_database
from pause_monitor.stress import StressBreakdown


def make_test_metrics(**kwargs) -> PowermetricsResult:
    """Create PowermetricsResult with sensible defaults for testing."""
    defaults = {
        "elapsed_ns": 100_000_000,
        "throttled": False,
        "cpu_power": 5.0,
        "gpu_pct": 10.0,
        "gpu_power": 1.0,
        "io_read_per_s": 1000.0,
        "io_write_per_s": 500.0,
        "wakeups_per_s": 50.0,
        "pageins_per_s": 0.0,
        "top_cpu_processes": [],
        "top_pagein_processes": [],
        "top_wakeup_processes": [],
        "top_diskio_processes": [],
    }
    defaults.update(kwargs)
    return PowermetricsResult(**defaults)


def make_test_config(tmp_path: Path) -> Config:
    """Create a Config with paths pointing to tmp_path."""
    config = Config()
    config.sentinel = SentinelConfig(
        fast_interval_ms=10,
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
        metrics = make_test_metrics()

        # Add samples from different tiers using push(metrics, stress, tier)
        for tier in [1, 2, 2, 3, 3, 3]:
            stress = StressBreakdown(
                load=tier * 10, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0
            )
            buffer.push(metrics, stress, tier)

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
        metrics = make_test_metrics()

        # Add more samples than buffer can hold
        for i in range(10):
            stress = StressBreakdown(load=i, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
            buffer.push(metrics, stress, tier=1)

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
        metrics = make_test_metrics()
        stress = StressBreakdown(load=5, memory=25, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
        samples = [
            RingSample(timestamp=datetime.now(), metrics=metrics, stress=stress, tier=2)
            for _ in range(3)
        ]

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

        metrics = make_test_metrics()
        stress = StressBreakdown(load=30, memory=20, thermal=0, latency=0, io=15, gpu=0, wakeups=0)
        samples = [
            RingSample(timestamp=datetime.now(), metrics=metrics, stress=stress, tier=3)
            for _ in range(3)
        ]

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
        from datetime import timedelta

        from pause_monitor.storage import update_event_status

        db_path = tmp_path / "test.db"
        init_database(db_path)

        conn = sqlite3.connect(db_path)

        # Insert events with different statuses
        for i, status in enumerate(["unreviewed", "reviewed", "pinned", "dismissed"]):
            start_time = datetime.now() - timedelta(hours=i)
            event_id = create_event(conn, start_time)
            finalize_event(
                conn,
                event_id,
                end_timestamp=start_time + timedelta(seconds=2.5),
                peak_stress=85,
                peak_tier=2,
            )
            if status != "unreviewed":
                update_event_status(conn, event_id, status)

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

    def test_events_have_peak_stress(self, db_with_events):
        """Events correctly store and retrieve peak stress."""
        conn = sqlite3.connect(db_with_events)
        events = get_events(conn, limit=1)
        conn.close()

        assert events[0].peak_stress == 85
        assert events[0].peak_tier == 2


class TestDaemonTierIntegration:
    """Test daemon integration with TierManager."""

    @pytest.fixture
    def mock_config(self, tmp_path) -> Config:
        """Create config pointing to temp directory."""
        return make_test_config(tmp_path)

    def test_daemon_initializes_with_tier_manager(self, mock_config):
        """Daemon correctly initializes TierManager and RingBuffer."""
        from pause_monitor.daemon import Daemon

        daemon = Daemon(mock_config)

        # Verify TierManager and RingBuffer are initialized
        assert daemon.tier_manager is not None
        assert daemon.ring_buffer is not None
        # Verify thresholds are configured
        assert daemon.tier_manager.elevated_threshold == mock_config.tiers.elevated_threshold
        assert daemon.tier_manager.critical_threshold == mock_config.tiers.critical_threshold

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
    async def test_daemon_runs_forensics_on_pause(self, mock_config, tmp_path):
        """Daemon runs forensics capture on pause detection."""
        from unittest.mock import AsyncMock, patch

        from pause_monitor.daemon import Daemon
        from pause_monitor.ringbuffer import RingBuffer

        # Ensure events directory exists
        (tmp_path / "events").mkdir(parents=True, exist_ok=True)
        init_database(mock_config.db_path)

        daemon = Daemon(mock_config)
        daemon._conn = sqlite3.connect(mock_config.db_path)
        daemon.notifier = MagicMock()
        daemon.notifier.pause_detected = MagicMock()

        # Initialize ring buffer
        daemon.ring_buffer = RingBuffer(max_samples=100)

        # Add some samples to the ring buffer
        metrics = make_test_metrics()
        stress = StressBreakdown(load=40, memory=30, thermal=0, latency=0, io=0, gpu=15, wakeups=0)
        for _ in range(5):
            daemon.ring_buffer.push(metrics, stress, tier=3)

        # Mock _run_forensics to avoid actual file I/O
        with patch.object(daemon, "_run_forensics", new_callable=AsyncMock) as mock_forensics:
            # Handle pause (must be >= pause_min_duration, default 2.0)
            await daemon._handle_pause(
                actual_interval=5.0,
                expected_interval=0.1,
            )

            # Verify forensics was called with correct duration
            mock_forensics.assert_called_once()
            call_kwargs = mock_forensics.call_args.kwargs
            assert call_kwargs["duration"] == 5.0

        daemon._conn.close()
