# tests/test_logging.py
"""Tests for unified logging system."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rogue_hunter.ringbuffer import RingBuffer
from rogue_hunter.socket_client import SocketClient
from rogue_hunter.socket_server import SocketServer


@pytest.fixture
def short_tmp_path():
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_") as tmpdir:
        yield Path(tmpdir)


async def wait_until(condition, timeout=1.0, interval=0.01):
    """Wait until condition() returns True, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Condition not met within {timeout}s")
        await asyncio.sleep(interval)


class TestSocketClientSendMessage:
    """Tests for SocketClient.send_message() method."""

    @pytest.mark.asyncio
    async def test_send_message_basic(self, short_tmp_path):
        """SocketClient should send JSON-encoded messages with newline delimiter."""
        socket_path = short_tmp_path / "test.sock"
        received_messages = []

        async def handle_client(reader, writer):
            while True:
                line = await reader.readline()
                if not line:
                    break
                received_messages.append(json.loads(line.decode()))
            writer.close()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

        try:
            client = SocketClient(socket_path=socket_path)
            await client.connect()

            # Send a message
            await client.send_message({"type": "test", "value": 42})

            # Wait for message to be received
            await wait_until(lambda: len(received_messages) > 0)

            assert received_messages[0] == {"type": "test", "value": 42}

            await client.disconnect()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_send_message_when_not_connected(self, short_tmp_path):
        """send_message() should raise ConnectionError when not connected."""
        socket_path = short_tmp_path / "nonexistent.sock"
        client = SocketClient(socket_path=socket_path)

        with pytest.raises(ConnectionError):
            await client.send_message({"type": "test"})


class TestSocketServerBidirectional:
    """Tests for bidirectional socket communication."""

    @pytest.mark.asyncio
    async def test_server_receives_client_messages(self, short_tmp_path):
        """SocketServer should receive and process messages from clients."""
        socket_path = short_tmp_path / "test.sock"
        buffer = RingBuffer(max_samples=10)

        server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
        await server.start()

        try:
            # Connect as client
            reader, writer = await asyncio.open_unix_connection(str(socket_path))

            # Wait for initial_state message
            await asyncio.wait_for(reader.readline(), timeout=2.0)

            # Send a log message from "client"
            log_msg = {"type": "log", "level": "info", "event": "test_event", "extra": "data"}
            writer.write((json.dumps(log_msg) + "\n").encode())
            await writer.drain()

            # Give server time to process
            await asyncio.sleep(0.1)

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_handles_invalid_json(self, short_tmp_path):
        """SocketServer should handle invalid JSON gracefully."""
        socket_path = short_tmp_path / "test.sock"
        buffer = RingBuffer(max_samples=10)

        server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
        await server.start()

        try:
            # Connect as client
            reader, writer = await asyncio.open_unix_connection(str(socket_path))

            # Wait for initial_state message
            await asyncio.wait_for(reader.readline(), timeout=2.0)

            # Send invalid JSON
            writer.write(b"not valid json\n")
            await writer.drain()

            # Server should not crash - give it time to process
            await asyncio.sleep(0.1)

            # Server should still be running
            assert server._running

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


class TestLogMessageHandler:
    """Tests for SocketServer log message handling."""

    @pytest.mark.asyncio
    async def test_handle_log_message_calls_structlog(self, short_tmp_path):
        """SocketServer should log received log messages via structlog."""
        socket_path = short_tmp_path / "test.sock"
        buffer = RingBuffer(max_samples=10)

        server = SocketServer(socket_path=socket_path, ring_buffer=buffer)

        # Capture the log call
        with patch.object(server, "_handle_log_message") as mock_handler:
            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(str(socket_path))

                # Read initial_state
                await asyncio.wait_for(reader.readline(), timeout=2.0)

                # Send log message
                msg = {"type": "log", "level": "info", "event": "test_event"}
                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()

                await asyncio.sleep(0.1)

                # Verify handler was called
                mock_handler.assert_called_once()
                call_args = mock_handler.call_args[0][0]
                assert call_args["type"] == "log"
                assert call_args["level"] == "info"
                assert call_args["event"] == "test_event"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()
