"""Tests for daemon core."""

import signal
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pause_monitor.config import Config
from pause_monitor.daemon import Daemon, DaemonState


def test_daemon_state_initial():
    """DaemonState initializes with correct defaults."""
    state = DaemonState()

    assert state.running is False
    assert state.sample_count == 0
    assert state.last_sample_time is None
    assert state.current_stress == 0


def test_daemon_state_update_sample():
    """DaemonState updates on new sample."""
    state = DaemonState()

    state.update_sample(stress=25, timestamp=datetime.now())

    assert state.sample_count == 1
    assert state.current_stress == 25
    assert state.last_sample_time is not None


def test_daemon_state_elevated_duration():
    """DaemonState tracks elevated duration."""
    state = DaemonState()

    state.enter_elevated()
    assert state.elevated_since is not None

    duration = state.elevated_duration
    assert duration >= 0


def test_daemon_init_creates_components():
    """Daemon initializes all required components."""
    config = Config()
    daemon = Daemon(config)

    assert daemon.config is config
    assert daemon.state is not None
    assert daemon.policy is not None
    assert daemon.notifier is not None
    assert daemon.io_baseline is not None
    assert daemon.pause_detector is not None
    assert daemon.core_count > 0


@pytest.mark.asyncio
async def test_daemon_start_initializes_database(tmp_path: Path):
    """Daemon.start() initializes database."""
    config = Config()

    # Patch the property at the class level
    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            with patch.object(
                Config,
                "events_dir",
                new_callable=lambda: property(lambda self: tmp_path / "events"),
            ):
                daemon = Daemon(config)

                with patch.object(daemon, "_run_loop", new_callable=AsyncMock):
                    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
                        # Start and immediately stop
                        daemon._shutdown_event.set()
                        await daemon.start()

                        assert (tmp_path / "test.db").exists()


@pytest.mark.asyncio
async def test_daemon_stop_cleans_up(tmp_path: Path):
    """Daemon.stop() cleans up resources."""
    config = Config()
    daemon = Daemon(config)

    # Mock powermetrics
    mock_powermetrics = AsyncMock()
    mock_powermetrics.stop = AsyncMock()
    daemon._powermetrics = mock_powermetrics

    await daemon.stop()

    # Check that stop was called (before _powermetrics was set to None)
    mock_powermetrics.stop.assert_called_once()
    # Verify cleanup happened
    assert daemon._powermetrics is None
    assert daemon.state.running is False


@pytest.mark.asyncio
async def test_daemon_handles_sigterm():
    """Daemon handles SIGTERM gracefully."""
    config = Config()
    daemon = Daemon(config)

    # Trigger SIGTERM handler
    daemon._handle_signal(signal.SIGTERM)

    assert daemon._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_daemon_collects_sample(tmp_path: Path):
    """Daemon collects and stores samples."""
    config = Config()

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            events_prop = lambda: property(lambda self: tmp_path / "events")  # noqa: E731
            with patch.object(Config, "events_dir", new_callable=events_prop):
                daemon = Daemon(config)

                # Initialize database
                from pause_monitor.storage import init_database

                init_database(config.db_path)
                daemon._conn = sqlite3.connect(config.db_path)

                # Mock powermetrics result
                from pause_monitor.collector import PowermetricsResult

                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=10.0,
                )

                sample = await daemon._collect_sample(pm_result, interval=5.0)

                assert sample is not None
                assert sample.cpu_pct == 25.0
                assert sample.stress.total >= 0


@pytest.mark.asyncio
async def test_daemon_detects_pause(tmp_path: Path):
    """Daemon detects and handles pause events."""
    config = Config()

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            events_prop = lambda: property(lambda self: tmp_path / "events")  # noqa: E731
            with patch.object(Config, "events_dir", new_callable=events_prop):
                daemon = Daemon(config)

                from pause_monitor.storage import init_database

                init_database(config.db_path)
                daemon._conn = sqlite3.connect(config.db_path)

                # Simulate a long interval (pause)
                daemon.pause_detector.expected_interval = 5.0

                # Mock was_recently_asleep to avoid pmset log encoding issues
                with patch("pause_monitor.daemon.was_recently_asleep", return_value=None):
                    with patch.object(
                        daemon, "_handle_pause", new_callable=AsyncMock
                    ) as mock_handle:
                        await daemon._check_for_pause(actual_interval=15.0)

                        mock_handle.assert_called_once()
