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

from rogue_hunter.collector import (
    LibprocCollector,
    ProcessSamples,
)
from rogue_hunter.config import Config
from rogue_hunter.daemon import Daemon, DaemonState

# Test constants
TEST_TIMESTAMP = 1706000000.0  # 2024-01-23 UTC


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


def _patch_config_paths(stack: ExitStack, base_path: Path, config_path: Path) -> None:
    """Apply all Config path property patches to the given ExitStack.

    This helper eliminates deep nested `with patch.object()` blocks by using
    ExitStack to manage multiple patches as a flat list.

    Args:
        stack: ExitStack to register patches with
        base_path: Directory to use for data paths (db, pid, socket)
        config_path: Path to the config file
    """
    # fmt: off
    stack.enter_context(patch.object(
        Config, "config_path",
        new_callable=lambda: property(lambda self: config_path)
    ))
    stack.enter_context(patch.object(
        Config, "data_dir",
        new_callable=lambda: property(lambda self: base_path)
    ))
    stack.enter_context(patch.object(
        Config, "db_path",
        new_callable=lambda: property(lambda self: base_path / "test.db")
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


def load_config_from_file(tmp_path: Path, **overrides) -> Config:
    """Create a config file with defaults (plus overrides) and load it.

    This exercises the actual Config.load() code path that production uses.
    Tests should use this instead of Config() to catch config loading bugs.
    """
    config_path = tmp_path / "config.toml"

    # Create config with any overrides, save to file
    config = Config(**overrides) if overrides else Config()
    config.save(config_path)

    # Load it back - this is what production does
    return Config.load(config_path)


@pytest.fixture
def patched_config_paths(tmp_path: Path) -> Iterator[Path]:
    """Fixture that patches all Config path properties to use tmp_path.

    Creates a real config file and patches Config.load() paths.
    Yields the base path for tests that need to reference it directly.
    """
    config_path = tmp_path / "config.toml"
    # Create default config file
    Config().save(config_path)

    with ExitStack() as stack:
        _patch_config_paths(stack, tmp_path, config_path)
        yield tmp_path


@pytest.fixture
def patched_config_short_paths(short_tmp_path: Path) -> Iterator[Path]:
    """Fixture that patches Config paths using short paths for Unix sockets.

    Use this instead of patched_config_paths when tests involve socket operations.
    """
    config_path = short_tmp_path / "config.toml"
    # Create default config file
    Config().save(config_path)

    with ExitStack() as stack:
        _patch_config_paths(stack, short_tmp_path, config_path)
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


def test_daemon_init_creates_components():
    """Daemon initializes all required components."""
    config = Config()
    daemon = Daemon(config)

    assert daemon.config is config
    assert daemon.state is not None
    assert daemon.ring_buffer is not None
    assert daemon.collector is not None


@pytest.mark.asyncio
async def test_daemon_start_initializes_database(patched_config_paths):
    """Daemon.start() initializes database."""
    config = Config.load()  # Load from file created by fixture
    daemon = Daemon(config)

    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        # Mock socket server (path too long for Unix sockets in tmp_path)
        with patch("rogue_hunter.daemon.SocketServer") as mock_socket_class:
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
    """Returns True when PID file contains current process ID and cmdline matches."""
    config = Config()
    pid_file = tmp_path / "daemon.pid"
    # Write current process PID - this process definitely exists
    pid_file.write_text(str(os.getpid()))

    with (
        patch.object(Config, "pid_path", new_callable=lambda: property(lambda self: pid_file)),
        patch("psutil.Process") as mock_process_class,
    ):
        mock_proc = mock_process_class.return_value
        mock_proc.cmdline.return_value = ["python", "-m", "rogue_hunter.cli", "daemon"]

        daemon = Daemon(config)
        result = daemon._check_already_running()

        assert result is True
        # PID file should still exist
        assert pid_file.exists()


@pytest.mark.asyncio
async def test_daemon_start_rejects_duplicate(patched_config_paths):
    """Daemon.start() raises if already running."""
    config = Config.load()
    pid_file = patched_config_paths / "daemon.pid"
    # Simulate existing running daemon (use current PID)
    pid_file.write_text(str(os.getpid()))

    daemon = Daemon(config)

    # Mock cmdline to look like rogue-hunter daemon
    with patch("psutil.Process") as mock_process_class:
        mock_proc = mock_process_class.return_value
        mock_proc.cmdline.return_value = ["python", "-m", "rogue_hunter.cli", "daemon"]

        with pytest.raises(RuntimeError, match="already running"):
            await daemon.start()


@pytest.mark.asyncio
async def test_daemon_start_writes_pid_file(patched_config_paths):
    """Daemon.start() writes PID file."""
    config = Config.load()
    pid_file = patched_config_paths / "daemon.pid"
    daemon = Daemon(config)

    with patch.object(daemon, "_start_caffeinate", new_callable=AsyncMock):
        # Mock socket server (path too long for Unix sockets in tmp_path)
        with patch("rogue_hunter.daemon.SocketServer") as mock_socket_class:
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
    from rogue_hunter.storage import init_database

    config = Config.load()
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Patch prune_old_data to track calls and signal shutdown after first call
    # Returns int (events_deleted) - system_samples table was removed
    with patch("rogue_hunter.daemon.prune_old_data", return_value=0) as mock_prune:

        def prune_side_effect(*args, **kwargs):
            daemon._shutdown_event.set()
            return 0

        mock_prune.side_effect = prune_side_effect

        # Create a side effect that properly closes the unawaited coroutine
        call_count = 0

        async def mock_wait_for_impl(coro, timeout):
            nonlocal call_count
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise asyncio.CancelledError()

        with patch("rogue_hunter.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
            try:
                await daemon._auto_prune()
            except asyncio.CancelledError:
                pass

        mock_prune.assert_called_once_with(
            daemon._conn,
            events_days=config.retention.events_days,
        )


@pytest.mark.asyncio
async def test_auto_prune_exits_on_shutdown(patched_config_paths):
    """Auto-prune exits cleanly when shutdown event is set."""
    config = Config.load()
    daemon = Daemon(config)

    # Set shutdown event immediately
    daemon._shutdown_event.set()

    # Track that prune_old_data is NOT called
    with patch("rogue_hunter.daemon.prune_old_data") as mock_prune:
        await daemon._auto_prune()
        mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_skips_if_no_connection(patched_config_paths):
    """Auto-prune skips pruning if database connection is None."""
    config = Config.load()
    daemon = Daemon(config)
    daemon._conn = None  # No connection

    with patch("rogue_hunter.daemon.prune_old_data") as mock_prune:
        # Patch wait_for to timeout once, then we set shutdown

        async def mock_wait_for_impl(coro, timeout):
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            # After first timeout, set shutdown so loop exits
            daemon._shutdown_event.set()
            raise asyncio.TimeoutError()

        with patch("rogue_hunter.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
            # Run actual _auto_prune - since _conn is None, prune should not be called
            await daemon._auto_prune()

        mock_prune.assert_not_called()


@pytest.mark.asyncio
async def test_auto_prune_uses_config_retention_days(patched_config_paths):
    """Auto-prune uses retention days from config."""
    from rogue_hunter.config import RetentionConfig
    from rogue_hunter.storage import init_database

    # Save custom config and load it (exercises actual load path)
    config = Config(retention=RetentionConfig(events_days=14))
    config.save(patched_config_paths / "config.toml")
    config = Config.load()
    daemon = Daemon(config)

    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Patch prune_old_data to track calls and signal shutdown after first call
    # Returns int (events_deleted) - system_samples table was removed
    with patch("rogue_hunter.daemon.prune_old_data", return_value=0) as mock_prune:

        def prune_side_effect(*args, **kwargs):
            daemon._shutdown_event.set()
            return 0

        mock_prune.side_effect = prune_side_effect

        # Create a side effect that properly closes the unawaited coroutine
        call_count = 0

        async def mock_wait_for_impl(coro, timeout):
            nonlocal call_count
            coro.close()  # Close the coroutine to prevent "never awaited" warning
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise asyncio.CancelledError()

        with patch("rogue_hunter.daemon.asyncio.wait_for", side_effect=mock_wait_for_impl):
            try:
                await daemon._auto_prune()
            except asyncio.CancelledError:
                pass

        mock_prune.assert_called_once_with(
            daemon._conn,
            events_days=14,
        )


# === Main Loop Tests ===


@pytest.mark.asyncio
async def test_daemon_uses_main_loop(patched_config_paths):
    """Daemon runs _main_loop for powermetrics-driven monitoring."""
    config = Config.load()
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
            with patch("rogue_hunter.daemon.SocketServer") as mock_socket_class:
                mock_socket = AsyncMock()
                mock_socket_class.return_value = mock_socket
                # Start daemon - it should call _main_loop and return
                await daemon.start()

    # Verify _main_loop was called (not sentinel.start())
    assert main_loop_called


# Note: Pause detection and _handle_pause were removed from the daemon.
# Forensics are now triggered by ProcessTracker band transitions (entering high/critical).
# ProcessTracker is tested in test_tracker.py.


# === Socket Server Integration Tests ===


@pytest.mark.asyncio
async def test_daemon_socket_available_after_start(patched_config_short_paths, monkeypatch):
    """Daemon should have socket server listening after start."""
    config = Config.load()
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


# === Collector Integration Tests ===


def test_daemon_uses_libproc_collector(patched_config_paths):
    """Daemon should use LibprocCollector."""
    config = Config.load()
    daemon = Daemon(config)

    assert hasattr(daemon, "collector")
    assert isinstance(daemon.collector, LibprocCollector)


@pytest.mark.asyncio
async def test_daemon_main_loop_collects_samples(patched_config_paths, monkeypatch):
    """Main loop should collect and process samples.

    This explicitly tests the "no tracker" case: daemon is created before DB exists,
    so tracker remains None. The main loop should still work without tracker.update().
    Tracker integration is tested separately in test_daemon_main_loop_updates_tracker.
    """
    from rogue_hunter.storage import init_database

    config = Config.load()
    daemon = Daemon(config)

    # Verify tracker is None (DB didn't exist at daemon init time)
    assert daemon.tracker is None

    # Initialize database to prevent NoneType errors in other code paths
    init_database(config.db_path)
    daemon._conn = sqlite3.connect(config.db_path)

    # Track samples pushed to ring buffer
    pushed_samples = []
    original_push = daemon.ring_buffer.push

    def track_push(samples):
        pushed_samples.append(samples)
        return original_push(samples)

    monkeypatch.setattr(daemon.ring_buffer, "push", track_push)

    # Create mock samples using shared helper
    from tests.conftest import make_process_score

    rogue1 = make_process_score(
        pid=123,
        command="test_proc",
        captured_at=TEST_TIMESTAMP,
        cpu=50.0,
        mem=1024 * 1024 * 100,
        score=25,
        band="medium",
    )
    rogue2 = make_process_score(
        pid=456,
        command="heavy_proc",
        captured_at=TEST_TIMESTAMP,
        cpu=80.0,
        mem=1024 * 1024 * 500,
        score=45,
        band="elevated",
    )
    mock_samples = [
        ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=1000,
            process_count=100,
            max_score=25,
            rogues=[rogue1],
            all_by_pid={rogue1.pid: rogue1},
        ),
        ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=1000,
            process_count=100,
            max_score=45,
            rogues=[rogue2],
            all_by_pid={rogue2.pid: rogue2},
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
                all_by_pid={},
            )
        return mock_samples[call_count - 1]

    monkeypatch.setattr(daemon.collector, "collect", mock_collect)

    # Run main loop (will exit after samples exhausted due to shutdown)
    await daemon._main_loop()

    # Should have pushed 2 samples (the 3rd triggers shutdown)
    assert len(pushed_samples) == 2
    assert pushed_samples[0].max_score == 25
    assert pushed_samples[1].max_score == 45


# === Task 8: ProcessTracker Integration Tests ===


@pytest.mark.asyncio
async def test_daemon_initializes_tracker(patched_config_paths, monkeypatch):
    """Daemon creates ProcessTracker on startup."""
    config = Config.load()

    monkeypatch.setattr("rogue_hunter.daemon.get_boot_time", lambda: int(TEST_TIMESTAMP))

    daemon = Daemon(config)
    # tracker is None until _init_database() is called
    assert daemon.tracker is None

    await daemon._init_database()

    assert daemon.tracker is not None
    assert daemon.boot_time == int(TEST_TIMESTAMP)


@pytest.mark.asyncio
async def test_daemon_schema_mismatch_recovery(patched_config_paths, monkeypatch):
    """Daemon handles incompatible DB schema: tracker=None at init, created in _init_database."""
    config = Config.load()

    # Create a DB with incompatible schema (missing required tables)
    db_path = config.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE fake_table (id INTEGER)")
    conn.close()

    monkeypatch.setattr("rogue_hunter.daemon.get_boot_time", lambda: int(TEST_TIMESTAMP))

    # Daemon __init__ should catch the OperationalError and leave tracker as None
    daemon = Daemon(config)
    assert daemon.tracker is None
    assert daemon._conn is None

    # _init_database should recreate DB and properly initialize tracker
    await daemon._init_database()

    assert daemon._conn is not None
    assert daemon.tracker is not None


@pytest.mark.asyncio
async def test_daemon_main_loop_updates_tracker(patched_config_paths, monkeypatch):
    """Main loop should call tracker.update() with processes above threshold."""
    config = Config.load()

    monkeypatch.setattr("rogue_hunter.daemon.get_boot_time", lambda: int(TEST_TIMESTAMP))

    daemon = Daemon(config)
    await daemon._init_database()  # Must init before accessing tracker

    # Track update calls
    update_calls = []
    original_update = daemon.tracker.update

    def track_update(scores):
        update_calls.append(list(scores))  # Copy since it's a generator result
        return original_update(scores)

    monkeypatch.setattr(daemon.tracker, "update", track_update)

    # Create mock samples - score must be >= tracking_threshold (35)
    from tests.conftest import make_process_score

    rogue = make_process_score(
        pid=123,
        command="test_proc",
        captured_at=TEST_TIMESTAMP,
        cpu=50.0,
        mem=1024 * 1024 * 100,
        score=40,  # Above tracking threshold (35)
        band="elevated",
    )
    mock_samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=100,
        max_score=40,
        rogues=[rogue],
        all_by_pid={rogue.pid: rogue},
    )

    # Mock collector.collect() to return sample then stop
    call_count = 0

    async def mock_collect():
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            daemon._shutdown_event.set()
        return mock_samples

    monkeypatch.setattr(daemon.collector, "collect", mock_collect)

    # Run main loop
    await daemon._main_loop()

    # Should have called tracker.update with processes above threshold
    assert len(update_calls) == 1
    assert len(update_calls[0]) == 1
    assert update_calls[0][0].pid == rogue.pid


# Note: Pause detection was removed from the daemon's main loop.
# The main loop now only: collect → track → buffer → broadcast.
# Forensics are triggered by ProcessTracker band transitions instead.
