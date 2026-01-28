"""Tests for daemon core."""

import asyncio
import os
import signal
import sqlite3
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from pause_monitor.collector import ProcessSamples, ProcessScore, TopCollector
from pause_monitor.config import Config
from pause_monitor.daemon import Daemon, DaemonState
from pause_monitor.ringbuffer import RingBuffer

# === Test Fixtures ===


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_") as tmpdir:
        yield Path(tmpdir)


def _patch_config_paths(stack: ExitStack, base_path: Path) -> None:
    """Apply all Config path property patches to the given ExitStack.

    This helper eliminates deep nested `with patch.object()` blocks by using
    ExitStack to manage multiple patches as a flat list.

    Args:
        stack: ExitStack to register patches with
        base_path: Directory to use for all Config paths
    """
    # fmt: off
    stack.enter_context(patch.object(
        Config, "data_dir",
        new_callable=lambda: property(lambda self: base_path)
    ))
    stack.enter_context(patch.object(
        Config, "db_path",
        new_callable=lambda: property(lambda self: base_path / "test.db")
    ))
    stack.enter_context(patch.object(
        Config, "events_dir",
        new_callable=lambda: property(lambda self: base_path / "events")
    ))
    stack.enter_context(patch.object(
        Config, "pid_path",
        new_callable=lambda: property(lambda self: base_path / "daemon.pid")
    ))
    stack.enter_context(patch.object(
        Config, "socket_path",
        new_callable=lambda: property(lambda self: base_path / "daemon.sock")
    ))
    # fmt: on


@pytest.fixture
def patched_config_paths(tmp_path: Path) -> Iterator[Path]:
    """Fixture that patches all Config path properties to use tmp_path.

    Yields the base path for tests that need to reference it directly.
    """
    with ExitStack() as stack:
        _patch_config_paths(stack, tmp_path)
        yield tmp_path


@pytest.fixture
def patched_config_short_paths(short_tmp_path: Path) -> Iterator[Path]:
    """Fixture that patches Config paths using short paths for Unix sockets.

    Use this instead of patched_config_paths when tests involve socket operations.
    """
    with ExitStack() as stack:
        _patch_config_paths(stack, short_tmp_path)
        yield short_tmp_path


def test_daemon_state_initial():
    """DaemonState initializes with correct defaults."""
    state = DaemonState()

    assert state.running is False
    assert state.sample_count == 0
    assert state.last_sample_time is None
    assert state.current_score == 0


def test_daemon_state_update_sample():
    """DaemonState updates on new sample."""
    state = DaemonState()

    state.update_sample(score=25)

    assert state.sample_count == 1
    assert state.current_score == 25
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
    assert daemon.notifier is not None
    assert daemon.ring_buffer is not None
    assert daemon.collector is not None


@pytest.mark.asyncio
async def test_daemon_start_initializes_database(patched_config_paths):
    """Daemon.start() initializes database."""
    config = Config()
    daemon = Daemon(config)

    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        # Mock socket server (path too long for Unix sockets in tmp_path)
        with patch("pause_monitor.daemon.SocketServer") as mock_socket_class:
            mock_socket = AsyncMock()
            mock_socket_class.return_value = mock_socket
            # Start and immediately stop
            daemon._shutdown_event.set()
            await daemon.start()

            assert (patched_config_paths / "test.db").exists()


@pytest.mark.asyncio
async def test_daemon_handles_sigterm():
    """Daemon handles SIGTERM gracefully."""
    config = Config()
    daemon = Daemon(config)

    # Trigger SIGTERM handler
    daemon._handle_signal(signal.SIGTERM)

    assert daemon._shutdown_event.is_set()


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
async def test_daemon_start_rejects_duplicate(patched_config_paths):
    """Daemon.start() raises if already running."""
    config = Config()
    pid_file = patched_config_paths / "daemon.pid"
    # Simulate existing running daemon (use current PID)
    pid_file.write_text(str(os.getpid()))

    daemon = Daemon(config)

    with pytest.raises(RuntimeError, match="already running"):
        await daemon.start()


@pytest.mark.asyncio
async def test_daemon_start_writes_pid_file(patched_config_paths):
    """Daemon.start() writes PID file."""
    config = Config()
    pid_file = patched_config_paths / "daemon.pid"
    daemon = Daemon(config)

    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        # Mock socket server (path too long for Unix sockets in tmp_path)
        with patch("pause_monitor.daemon.SocketServer") as mock_socket_class:
            mock_socket = AsyncMock()
            mock_socket_class.return_value = mock_socket
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

        await daemon.stop()

        assert not pid_file.exists()


# Auto-prune tests


@pytest.mark.asyncio
async def test_auto_prune_runs_on_timeout(patched_config_paths):
    """Auto-prune runs prune_old_data when timeout expires."""
    from pause_monitor.storage import init_database

    config = Config()
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Patch prune_old_data to track calls and signal shutdown after first call
    with patch("pause_monitor.daemon.prune_old_data", return_value=(0, 0)) as mock_prune:
        mock_prune.side_effect = lambda *args, **kwargs: (
            daemon._shutdown_event.set(),
            (0, 0),
        )[1]

        # Create a side effect that properly closes the unawaited coroutine
        call_count = 0

        async def mock_wait_for_impl(coro, timeout):
            nonlocal call_count
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise asyncio.CancelledError()

        with patch("pause_monitor.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
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
async def test_auto_prune_exits_on_shutdown(patched_config_paths):
    """Auto-prune exits cleanly when shutdown event is set."""
    config = Config()
    daemon = Daemon(config)

    # Set shutdown event immediately
    daemon._shutdown_event.set()

    # Track that prune_old_data is NOT called
    with patch("pause_monitor.daemon.prune_old_data") as mock_prune:
        await daemon._auto_prune()
        mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_skips_if_no_connection(patched_config_paths):
    """Auto-prune skips pruning if database connection is None."""
    config = Config()
    daemon = Daemon(config)
    daemon._conn = None  # No connection

    with patch("pause_monitor.daemon.prune_old_data") as mock_prune:
        # Patch wait_for to timeout once, then we set shutdown

        async def mock_wait_for_impl(coro, timeout):
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            # After first timeout, set shutdown so loop exits
            daemon._shutdown_event.set()
            raise asyncio.TimeoutError()

        with patch("pause_monitor.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
            # Run actual _auto_prune - since _conn is None, prune should not be called
            await daemon._auto_prune()

        mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_uses_config_retention_days(patched_config_paths):
    """Auto-prune uses retention days from config."""
    from pause_monitor.config import RetentionConfig
    from pause_monitor.storage import init_database

    config = Config(
        retention=RetentionConfig(samples_days=7, events_days=14),
    )
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Patch prune_old_data to track calls and signal shutdown after first call
    with patch("pause_monitor.daemon.prune_old_data", return_value=(0, 0)) as mock_prune:
        mock_prune.side_effect = lambda *args, **kwargs: (
            daemon._shutdown_event.set(),
            (0, 0),
        )[1]

        # Create a side effect that properly closes the unawaited coroutine
        call_count = 0

        async def mock_wait_for_impl(coro, timeout):
            nonlocal call_count
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise asyncio.CancelledError()

        with patch("pause_monitor.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
            try:
                await daemon._auto_prune()
            except asyncio.CancelledError:
                pass

        mock_prune.assert_called_once_with(
            daemon._conn,
            samples_days=7,
            events_days=14,
        )


# === Main Loop Tests ===


@pytest.mark.asyncio
async def test_daemon_uses_main_loop(patched_config_paths):
    """Daemon runs _main_loop for powermetrics-driven monitoring."""
    config = Config()
    daemon = Daemon(config)

    # Verify ring_buffer is initialized
    assert daemon.ring_buffer is not None

    # Track if _main_loop was called
    main_loop_called = False

    async def mock_main_loop():
        nonlocal main_loop_called
        main_loop_called = True
        # Immediately return to end daemon
        return

    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        with patch.object(daemon, "_main_loop", side_effect=mock_main_loop):
            # Mock socket server (path too long for Unix sockets in tmp_path)
            with patch("pause_monitor.daemon.SocketServer") as mock_socket_class:
                mock_socket = AsyncMock()
                mock_socket_class.return_value = mock_socket
                # Start daemon - it should call _main_loop and return
                await daemon.start()

    # Verify _main_loop was called (not sentinel.start())
    assert main_loop_called


@pytest.mark.asyncio
async def test_daemon_handles_pause_with_forensics(patched_config_paths):
    """Daemon handles pause by running forensics capture."""
    from pause_monitor.storage import init_database

    config = Config()
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Initialize ring buffer so freeze() works
    daemon.ring_buffer = RingBuffer(max_samples=100)

    # Mock was_recently_asleep and _run_forensics
    # Note: actual duration must be >= config.alerts.pause_min_duration (default 2.0)
    with patch("pause_monitor.daemon.was_recently_asleep", return_value=None):
        with patch.object(daemon, "_run_forensics", new_callable=AsyncMock) as mock_forensics:
            await daemon._handle_pause(
                elapsed_ms=2500,  # 2.5 seconds in ms
                expected_ms=100,
            )

            # Verify forensics was called
            mock_forensics.assert_called_once()
            # First arg should be the frozen buffer contents
            call_args = mock_forensics.call_args
            assert call_args.kwargs.get("duration") == 2.5  # Converted to seconds


# Note: Per-process tracking via ProcessTracker replaces the tier system.
# ProcessTracker is tested in test_tracker.py.
# ProcessTracker integration with daemon is in Task 8.


# === Socket Server Integration Tests ===


@pytest.mark.asyncio
async def test_daemon_socket_available_after_start(patched_config_short_paths, monkeypatch):
    """Daemon should have socket server listening after start."""
    config = Config()
    daemon = Daemon(config)

    # Mock _main_loop to exit immediately (we just want to test socket wiring)
    async def mock_main_loop():
        pass

    monkeypatch.setattr(daemon, "_main_loop", mock_main_loop)

    # Mock caffeinate to avoid actual subprocess
    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        # Start daemon (will return after mock_main_loop completes)
        await daemon.start()

    # Socket file should exist and server should be listening
    assert config.socket_path.exists(), "Socket file should exist after daemon start"

    # Verify we can connect (reader unused, only testing connection)
    _, writer = await asyncio.open_unix_connection(str(config.socket_path))
    writer.close()
    await writer.wait_closed()

    await daemon.stop()
    assert not config.socket_path.exists(), "Socket file should be cleaned up after stop"


# === Task 10: TopCollector Integration Tests ===


def test_daemon_uses_top_collector(patched_config_paths):
    """Daemon should use TopCollector instead of PowermetricsStream."""
    config = Config()
    daemon = Daemon(config)

    assert hasattr(daemon, "collector")
    assert isinstance(daemon.collector, TopCollector)


@pytest.mark.asyncio
async def test_daemon_main_loop_collects_samples(patched_config_paths, monkeypatch):
    """Main loop should collect and process samples via TopCollector."""
    from pause_monitor.storage import init_database

    config = Config()
    daemon = Daemon(config)

    # Initialize database to prevent NoneType errors
    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Track samples pushed to ring buffer
    pushed_samples = []
    original_push = daemon.ring_buffer.push

    def track_push(samples, tier):
        pushed_samples.append((samples, tier))
        return original_push(samples, tier)

    monkeypatch.setattr(daemon.ring_buffer, "push", track_push)

    # Create mock samples
    mock_samples = [
        ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=1000,
            process_count=100,
            max_score=25,
            rogues=[
                ProcessScore(
                    pid=123,
                    command="test_proc",
                    cpu=50.0,
                    state="running",
                    mem=1024 * 1024 * 100,
                    cmprs=0,
                    pageins=10,
                    csw=500,
                    sysbsd=200,
                    threads=5,
                    score=25,
                    categories=frozenset(["cpu"]),
                    captured_at=1706000000.0,
                )
            ],
        ),
        ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=1000,
            process_count=100,
            max_score=45,
            rogues=[
                ProcessScore(
                    pid=456,
                    command="heavy_proc",
                    cpu=80.0,
                    state="running",
                    mem=1024 * 1024 * 500,
                    cmprs=1024 * 1024 * 50,
                    pageins=100,
                    csw=5000,
                    sysbsd=2000,
                    threads=20,
                    score=45,
                    categories=frozenset(["cpu", "mem"]),
                    captured_at=1706000000.0,
                )
            ],
        ),
    ]

    # Mock collector.collect() to return samples then stop
    call_count = 0

    async def mock_collect():
        nonlocal call_count
        call_count += 1
        if call_count > len(mock_samples):
            daemon._shutdown_event.set()
            # Return empty sample when shutting down
            return ProcessSamples(
                timestamp=datetime.now(),
                elapsed_ms=1000,
                process_count=0,
                max_score=0,
                rogues=[],
            )
        return mock_samples[call_count - 1]

    monkeypatch.setattr(daemon.collector, "collect", mock_collect)

    # Run main loop (will exit after samples exhausted due to shutdown)
    await daemon._main_loop()

    # Should have pushed 2 samples (the 3rd triggers shutdown)
    assert len(pushed_samples) == 2
    assert pushed_samples[0][0].max_score == 25
    assert pushed_samples[1][0].max_score == 45


@pytest.mark.asyncio
async def test_daemon_main_loop_handles_pause_detection(patched_config_paths, monkeypatch):
    """Main loop should detect pauses from elapsed_ms."""
    from pause_monitor.storage import init_database

    config = Config()
    config.alerts.pause_min_duration = 0.1  # Lower threshold for testing
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Track pause handler calls
    pause_calls = []

    async def mock_handle_pause(elapsed_ms, expected_ms):
        pause_calls.append((elapsed_ms, expected_ms))

    monkeypatch.setattr(daemon, "_handle_pause", mock_handle_pause)

    # Create a sample with long elapsed_ms indicating a pause
    # pause_threshold_ratio default is 2.0, expected is 1500ms
    # So 4500ms elapsed (3x expected) should trigger pause detection
    pause_sample = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=4500,  # 3x expected = definitely a pause
        process_count=100,
        max_score=20,
        rogues=[],
    )

    call_count = 0

    async def mock_collect():
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            daemon._shutdown_event.set()
        return pause_sample

    monkeypatch.setattr(daemon.collector, "collect", mock_collect)

    # Run main loop
    await daemon._main_loop()

    # Should have called pause handler
    assert len(pause_calls) == 1
    assert pause_calls[0][0] == 4500  # elapsed_ms
    assert pause_calls[0][1] == 1500  # expected_ms
