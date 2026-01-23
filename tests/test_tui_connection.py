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

        # Create a mock server that sends initial state
        async def handle_client(reader, writer):
            msg = {
                "type": "initial_state",
                "samples": [],
                "tier": 1,
                "current_stress": {
                    "load": 5,
                    "memory": 10,
                    "thermal": 0,
                    "latency": 0,
                    "io": 0,
                    "gpu": 0,
                    "wakeups": 0,
                    "pageins": 0,
                    "total": 15,
                },
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


def test_tui_handle_socket_data_updates_stress():
    """TUI should update widgets with socket data."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)

    # Create mock widgets
    mock_gauge = MagicMock()
    mock_breakdown = MagicMock()
    mock_metrics = MagicMock()
    mock_processes = MagicMock()

    # Patch query_one to return our mocks
    def mock_query_one(selector, widget_type=None):
        if selector == "#stress-gauge":
            return mock_gauge
        elif selector == "#breakdown":
            return mock_breakdown
        elif selector == "#metrics":
            return mock_metrics
        elif selector == "#processes":
            return mock_processes
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Test data from socket
    data = {
        "type": "sample",
        "tier": 2,
        "stress": {
            "load": 10,
            "memory": 20,
            "thermal": 5,
            "latency": 0,
            "io": 3,
            "gpu": 8,
            "wakeups": 2,
            "pageins": 5,
            "total": 53,
        },
        "metrics": {
            "cpu_power": 25.5,
            "pageins_per_s": 100.0,
            "throttled": False,
            "top_cpu_processes": [
                {"name": "test_proc", "pid": 123, "cpu_ms_per_s": 500.0},
            ],
            "top_pagein_processes": [
                {"name": "swap_proc", "pid": 456, "pageins_per_s": 50.0},
            ],
        },
    }

    app._handle_socket_data(data)

    # Verify stress gauge was updated
    mock_gauge.update_stress.assert_called_once_with(53)

    # Verify processes panel was updated
    mock_processes.update_processes.assert_called_once_with(
        cpu_processes=[{"name": "test_proc", "pid": 123, "cpu_ms_per_s": 500.0}],
        pagein_processes=[{"name": "swap_proc", "pid": 456, "pageins_per_s": 50.0}],
    )


def test_tui_handle_initial_state():
    """TUI should handle initial_state message from daemon."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)

    # Create mock widgets
    mock_gauge = MagicMock()
    mock_breakdown = MagicMock()
    mock_metrics = MagicMock()
    mock_processes = MagicMock()

    def mock_query_one(selector, widget_type=None):
        if selector == "#stress-gauge":
            return mock_gauge
        elif selector == "#breakdown":
            return mock_breakdown
        elif selector == "#metrics":
            return mock_metrics
        elif selector == "#processes":
            return mock_processes
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Initial state message
    data = {
        "type": "initial_state",
        "samples": [],
        "tier": 1,
        "current_stress": {
            "load": 5,
            "memory": 10,
            "thermal": 0,
            "latency": 0,
            "io": 0,
            "gpu": 0,
            "wakeups": 0,
            "pageins": 0,
            "total": 15,
        },
        "sample_count": 0,
    }

    app._handle_socket_data(data)

    # Verify stress gauge was updated with total
    mock_gauge.update_stress.assert_called_once_with(15)
