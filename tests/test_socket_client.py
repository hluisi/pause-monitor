# tests/test_socket_client.py
"""Tests for Unix socket client module."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from rogue_hunter.socket_client import SocketClient


@pytest.fixture
def short_tmp_path():
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_") as tmpdir:
        yield Path(tmpdir)


@pytest.mark.asyncio
async def test_socket_client_receives_data(short_tmp_path):
    """SocketClient should receive and parse messages."""
    socket_path = short_tmp_path / "test.sock"

    # Start mock server
    async def handle_client(reader, writer):
        msg = {"samples": [], "max_score": 50, "current_stress": {"load": 5}}
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

    try:
        client = SocketClient(socket_path=socket_path)
        await client.connect()

        # Read one message
        data = await client.read_message()
        assert data["max_score"] == 50

        await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_socket_client_raises_on_connection_failure(short_tmp_path):
    """SocketClient should raise FileNotFoundError if daemon not running."""
    socket_path = short_tmp_path / "nonexistent.sock"

    client = SocketClient(socket_path=socket_path)

    with pytest.raises(FileNotFoundError):
        await client.connect()
