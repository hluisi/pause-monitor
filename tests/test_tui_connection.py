# tests/test_tui_connection.py
"""Tests for TUI socket connection logic."""

import asyncio
import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from pause_monitor.config import Config


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_tui_") as tmpdir:
        yield Path(tmpdir)


def _patch_socket_path(stack: ExitStack, base_path: Path) -> None:
    """Patch Config.socket_path to use given base_path."""
    stack.enter_context(
        patch.object(
            Config,
            "socket_path",
            new_callable=lambda: property(lambda self: base_path / "daemon.sock"),
        )
    )


@pytest.mark.asyncio
async def test_tui_connects_via_socket_when_daemon_running(short_tmp_path: Path):
    """TUI should connect via socket when daemon is running."""
    from pause_monitor.tui.app import PauseMonitorApp

    with ExitStack() as stack:
        _patch_socket_path(stack, short_tmp_path)
        socket_path = short_tmp_path / "daemon.sock"

        # Create a mock server that sends initial state (new format)
        async def handle_client(reader, writer):
            msg = {
                "type": "initial_state",
                "samples": [],
                "tier": 1,
                "max_score": 15,
                "sample_count": 0,
            }
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
            # Wait for client to disconnect (EOF)
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

        try:
            config = Config()
            app = PauseMonitorApp(config)
            # Simulate mount - this should connect via socket
            await app._try_socket_connect()

            assert app._use_socket is True
            assert "(live)" in app.sub_title
        finally:
            # Disconnect client first - signals handler to exit
            if app._socket_client:
                await app._socket_client.disconnect()
            # Then close server after handler has cleaned up
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_tui_shows_waiting_state_when_no_daemon(short_tmp_path: Path):
    """TUI should show waiting state when daemon not running."""
    from pause_monitor.tui.app import PauseMonitorApp

    with ExitStack() as stack:
        _patch_socket_path(stack, short_tmp_path)
        socket_path = short_tmp_path / "daemon.sock"

        # No socket file (daemon not running)
        assert not socket_path.exists()

        config = Config()
        app = PauseMonitorApp(config)

        # Try to connect - should fail gracefully
        await app._try_socket_connect()

        assert app._use_socket is False
        assert "disconnected" in app.sub_title.lower()


def test_tui_set_disconnected_updates_subtitle():
    """TUI should show error state when daemon connection is lost."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)
    app.sub_title = "System Health Monitor (live)"
    app._use_socket = True

    # Simulate connection error
    app._set_disconnected()

    assert app._use_socket is False
    assert "disconnected" in app.sub_title.lower()


def test_tui_handle_socket_data_updates_score():
    """TUI should update widgets with socket data (new ProcessSamples format)."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)

    # Create mock widgets
    mock_gauge = MagicMock()
    mock_sample_info = MagicMock()
    mock_processes = MagicMock()

    # Patch query_one to return our mocks
    def mock_query_one(selector, widget_type=None):
        if selector == "#stress-gauge":
            return mock_gauge
        elif selector == "#sample-info":
            return mock_sample_info
        elif selector == "#processes":
            return mock_processes
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Test data from socket (new format with rogues)
    data = {
        "type": "sample",
        "timestamp": "2026-01-24T12:00:00",
        "tier": 2,
        "elapsed_ms": 50,
        "process_count": 500,
        "max_score": 75,
        "rogues": [
            {
                "pid": 123,
                "command": "test_proc",
                "cpu": 50.0,
                "state": "running",
                "mem": 1000000,
                "cmprs": 0,
                "pageins": 10,
                "csw": 100,
                "sysbsd": 50,
                "threads": 4,
                "score": 75,
                "categories": ["cpu"],
            },
        ],
        "sample_count": 15,
    }

    app._handle_socket_data(data)

    # Verify score gauge was updated with max_score
    mock_gauge.update_score.assert_called_once_with(75)

    # Verify sample info panel was updated
    mock_sample_info.update_info.assert_called_once_with(2, 500, 15)

    # Verify processes panel was updated with rogues
    mock_processes.update_rogues.assert_called_once_with(data["rogues"])


def test_tui_handle_initial_state():
    """TUI should handle initial_state message from daemon (new format)."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)

    # Create mock widgets
    mock_gauge = MagicMock()
    mock_sample_info = MagicMock()
    mock_processes = MagicMock()

    def mock_query_one(selector, widget_type=None):
        if selector == "#stress-gauge":
            return mock_gauge
        elif selector == "#sample-info":
            return mock_sample_info
        elif selector == "#processes":
            return mock_processes
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Initial state message (new format)
    data = {
        "type": "initial_state",
        "samples": [
            {
                "timestamp": "2026-01-24T12:00:00",
                "tier": 1,
                "elapsed_ms": 45,
                "process_count": 400,
                "max_score": 25,
                "rogues": [
                    {
                        "pid": 1,
                        "command": "init",
                        "cpu": 0.1,
                        "state": "idle",
                        "mem": 1000,
                        "cmprs": 0,
                        "pageins": 0,
                        "csw": 1,
                        "sysbsd": 0,
                        "threads": 1,
                        "score": 25,
                        "categories": [],
                    },
                ],
            },
        ],
        "tier": 1,
        "max_score": 25,
        "sample_count": 1,
    }

    app._handle_socket_data(data)

    # Verify score gauge was updated with max_score
    mock_gauge.update_score.assert_called_once_with(25)

    # Verify sample info panel was updated
    mock_sample_info.update_info.assert_called_once_with(1, 400, 1)

    # Verify processes panel was updated with rogues from last sample
    mock_processes.update_rogues.assert_called_once_with(data["samples"][-1]["rogues"])
