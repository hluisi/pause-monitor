"""Tests for daemon core."""

import asyncio
import os
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


# PID file management tests


def test_write_pid_file(tmp_path: Path):
    """Daemon writes PID file with current process ID."""
    config = Config()

    with patch.object(
        Config, "pid_path", new_callable=lambda: property(lambda self: tmp_path / "daemon.pid")
    ):
        daemon = Daemon(config)
        daemon._write_pid_file()

        assert config.pid_path.exists()
        assert config.pid_path.read_text() == str(os.getpid())


def test_remove_pid_file(tmp_path: Path):
    """Daemon removes PID file."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        daemon._remove_pid_file()

        assert not pid_file.exists()


def test_remove_pid_file_nonexistent(tmp_path: Path):
    """Daemon handles removing nonexistent PID file gracefully."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        # Should not raise
        daemon._remove_pid_file()


def test_check_already_running_no_pid_file(tmp_path: Path):
    """Returns False when no PID file exists."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        assert daemon._check_already_running() is False


def test_check_already_running_stale_pid(tmp_path: Path):
    """Returns False and cleans up stale PID file."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    # Write a PID that doesn't exist (use a very high number)
    pid_file.write_text("999999999")

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        result = daemon._check_already_running()

        assert result is False
        # Stale PID file should be removed
        assert not pid_file.exists()


def test_check_already_running_invalid_pid(tmp_path: Path):
    """Returns False and cleans up PID file with invalid content."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("not-a-number")

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        result = daemon._check_already_running()

        assert result is False
        # Invalid PID file should be removed
        assert not pid_file.exists()


def test_check_already_running_current_process(tmp_path: Path):
    """Returns True when PID file contains current process ID."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    # Write current process PID - this process definitely exists
    pid_file.write_text(str(os.getpid()))

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)
        result = daemon._check_already_running()

        assert result is True
        # PID file should still exist
        assert pid_file.exists()


@pytest.mark.asyncio
async def test_daemon_start_rejects_duplicate(tmp_path: Path):
    """Daemon.start() raises if already running."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    # Simulate existing running daemon (use current PID)
    pid_file.write_text(str(os.getpid()))

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
            daemon = Daemon(config)

            with pytest.raises(RuntimeError, match="already running"):
                await daemon.start()


@pytest.mark.asyncio
async def test_daemon_start_writes_pid_file(tmp_path: Path):
    """Daemon.start() writes PID file."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            with patch.object(
                Config,
                "events_dir",
                new_callable=lambda: property(lambda self: tmp_path / "events"),
            ):
                with patch.object(
                    Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)
                ):
                    daemon = Daemon(config)

                    with patch.object(daemon, "_run_loop", new_callable=AsyncMock):
                        with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
                            daemon._shutdown_event.set()
                            await daemon.start()

                            assert pid_file.exists()
                            assert pid_file.read_text() == str(os.getpid())


@pytest.mark.asyncio
async def test_daemon_stop_removes_pid_file(tmp_path: Path):
    """Daemon.stop() removes PID file."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))

    with patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)):
        daemon = Daemon(config)

        # Mock powermetrics
        mock_powermetrics = AsyncMock()
        mock_powermetrics.stop = AsyncMock()
        daemon._powermetrics = mock_powermetrics

        await daemon.stop()

        assert not pid_file.exists()


# Auto-prune tests


@pytest.mark.asyncio
async def test_auto_prune_runs_on_timeout(tmp_path: Path):
    """Auto-prune runs prune_old_data when timeout expires."""
    config = Config()

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            daemon = Daemon(config)

            from pause_monitor.storage import init_database

            init_database(config.db_path)
            daemon._conn = sqlite3.connect(config.db_path)

            # Patch prune_old_data to track calls and signal shutdown after first call
            with patch("pause_monitor.daemon.prune_old_data", return_value=(0, 0)) as mock_prune:
                mock_prune.side_effect = lambda *args, **kwargs: (
                    daemon._shutdown_event.set(),
                    (0, 0),
                )[1]

                # Patch the timeout to be very short so we don't wait 24 hours
                with patch("pause_monitor.daemon.asyncio.wait_for") as mock_wait_for:
                    # First call times out (triggers prune), second would block but shutdown is set
                    mock_wait_for.side_effect = [asyncio.TimeoutError(), asyncio.CancelledError()]

                    # Run actual _auto_prune method - it will exit after prune sets shutdown
                    try:
                        await daemon._auto_prune()
                    except asyncio.CancelledError:
                        pass

                mock_prune.assert_called_once_with(
                    daemon._conn,
                    samples_days=config.retention.samples_days,
                    events_days=config.retention.events_days,
                )


@pytest.mark.asyncio
async def test_auto_prune_exits_on_shutdown(tmp_path: Path):
    """Auto-prune exits cleanly when shutdown event is set."""
    config = Config()

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        daemon = Daemon(config)

        # Set shutdown event immediately
        daemon._shutdown_event.set()

        # Track that prune_old_data is NOT called
        with patch("pause_monitor.daemon.prune_old_data") as mock_prune:
            await daemon._auto_prune()
            mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_skips_if_no_connection(tmp_path: Path):
    """Auto-prune skips pruning if database connection is None."""
    config = Config()

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        daemon = Daemon(config)
        daemon._conn = None  # No connection

        with patch("pause_monitor.daemon.prune_old_data") as mock_prune:
            # Patch wait_for to timeout once, then we set shutdown
            with patch("pause_monitor.daemon.asyncio.wait_for") as mock_wait_for:

                def timeout_then_shutdown(*args, **kwargs):
                    # After first timeout, set shutdown so loop exits
                    daemon._shutdown_event.set()
                    raise asyncio.TimeoutError()

                mock_wait_for.side_effect = timeout_then_shutdown

                # Run actual _auto_prune - since _conn is None, prune should not be called
                await daemon._auto_prune()

            mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_uses_config_retention_days(tmp_path: Path):
    """Auto-prune uses retention days from config."""
    from pause_monitor.config import RetentionConfig

    config = Config(
        retention=RetentionConfig(samples_days=7, events_days=14),
    )

    with patch.object(Config, "data_dir", new_callable=lambda: property(lambda self: tmp_path)):
        with patch.object(
            Config, "db_path", new_callable=lambda: property(lambda self: tmp_path / "test.db")
        ):
            daemon = Daemon(config)

            from pause_monitor.storage import init_database

            init_database(config.db_path)
            daemon._conn = sqlite3.connect(config.db_path)

            # Patch prune_old_data to track calls and signal shutdown after first call
            with patch("pause_monitor.daemon.prune_old_data", return_value=(0, 0)) as mock_prune:
                mock_prune.side_effect = lambda *args, **kwargs: (
                    daemon._shutdown_event.set(),
                    (0, 0),
                )[1]

                # Patch the timeout to be very short
                with patch("pause_monitor.daemon.asyncio.wait_for") as mock_wait_for:
                    mock_wait_for.side_effect = [asyncio.TimeoutError(), asyncio.CancelledError()]

                    try:
                        await daemon._auto_prune()
                    except asyncio.CancelledError:
                        pass

                mock_prune.assert_called_once_with(
                    daemon._conn,
                    samples_days=7,
                    events_days=14,
                )
