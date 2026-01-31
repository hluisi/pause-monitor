# tests/test_tui_connection.py
"""Tests for TUI socket connection logic."""

import asyncio
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from rogue_hunter.config import Config


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
    from rogue_hunter.tui.app import RogueHunterApp

    with ExitStack() as stack:
        _patch_socket_path(stack, short_tmp_path)
        socket_path = short_tmp_path / "daemon.sock"

        # Create a mock server that accepts connections
        async def handle_client(reader, writer):
            # Wait for client to disconnect (EOF)
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

        try:
            config = Config()
            app = RogueHunterApp(config)
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
    from rogue_hunter.tui.app import RogueHunterApp

    with ExitStack() as stack:
        _patch_socket_path(stack, short_tmp_path)
        socket_path = short_tmp_path / "daemon.sock"

        # No socket file (daemon not running)
        assert not socket_path.exists()

        config = Config()
        app = RogueHunterApp(config)

        # Try to connect - should fail gracefully
        await app._try_socket_connect()

        assert app._use_socket is False
        assert "disconnected" in app.sub_title.lower()


def test_tui_set_disconnected_updates_subtitle():
    """TUI should show error state when daemon connection is lost."""
    from rogue_hunter.tui.app import RogueHunterApp

    config = Config()
    app = RogueHunterApp(config)
    app.sub_title = "Real-time Dashboard (live)"
    app._use_socket = True

    # Simulate connection error (no reconnect since this is sync test without event loop)
    app._set_disconnected(start_reconnect=False)

    assert app._use_socket is False
    assert "disconnected" in app.sub_title.lower()


def test_tui_handle_socket_data_updates_widgets():
    """TUI should update widgets with socket data (new ProcessSamples format)."""
    from rogue_hunter.tui.app import RogueHunterApp

    config = Config()
    app = RogueHunterApp(config)

    # Create mock widgets
    mock_header = MagicMock()
    mock_process_table = MagicMock()
    mock_activity = MagicMock()
    mock_tracked = MagicMock()

    # Patch query_one to return our mocks
    def mock_query_one(selector, widget_type=None):
        if selector == "#header":
            return mock_header
        elif selector == "#main-area":
            return mock_process_table
        elif selector == "#activity":
            return mock_activity
        elif selector == "#tracked":
            return mock_tracked
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Test data from socket (new format with rogues)
    data = {
        "type": "sample",
        "timestamp": "2026-01-24T12:00:00",
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

    # Verify header was updated
    mock_header.update_from_sample.assert_called_once()
    call_args = mock_header.update_from_sample.call_args
    assert call_args[0][0] == 75  # max_score
    assert call_args[0][1] == 500  # process_count
    assert call_args[0][2] == 15  # sample_count

    # Verify process table was updated with rogues
    mock_process_table.update_rogues.assert_called_once()
    rogues_arg = mock_process_table.update_rogues.call_args[0][0]
    assert len(rogues_arg) == 1
    assert rogues_arg[0]["command"] == "test_proc"

    # Verify activity log was checked for transitions
    mock_activity.check_transitions.assert_called_once()
