# tests/test_socket_server.py
"""Tests for Unix socket server module."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.collector import ProcessSamples, ProcessScore
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.socket_server import SocketServer


async def wait_until(condition, timeout=1.0, interval=0.01):
    """Wait until condition() returns True, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Condition not met within {timeout}s")
        await asyncio.sleep(interval)


@pytest.fixture
def short_tmp_path():
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_") as tmpdir:
        yield Path(tmpdir)


def make_test_process_score(**kwargs) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    import time

    defaults = {
        "pid": 1,
        "command": "test",
        "cpu": 50.0,
        "state": "running",
        "mem": 1000,
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "score": 50,
        "categories": frozenset({"cpu"}),
        "captured_at": time.time(),
    }
    defaults.update(kwargs)
    return ProcessScore(**defaults)


def make_test_samples(**kwargs) -> ProcessSamples:
    """Create ProcessSamples with sensible defaults for testing."""
    defaults = {
        "timestamp": datetime.now(),
        "elapsed_ms": 1000,
        "process_count": 100,
        "max_score": 50,
        "rogues": [],
    }
    defaults.update(kwargs)
    return ProcessSamples(**defaults)


@pytest.mark.asyncio
async def test_socket_server_starts_and_stops(short_tmp_path):
    """SocketServer should start listening and stop cleanly."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)

    await server.start()
    assert socket_path.exists()

    await server.stop()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_socket_server_streams_initial_state_to_client(short_tmp_path):
    """SocketServer should send initial ring buffer state on connect."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    # Add samples using ProcessSamples
    samples = make_test_samples(
        max_score=75,
        rogues=[make_test_process_score(pid=123, command="heavy", score=75)],
    )
    buffer.push(samples)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read first message (initial state)
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        message = json.loads(data.decode())

        assert message["type"] == "initial_state"
        assert "samples" in message
        assert len(message["samples"]) == 1
        assert message["samples"][0]["max_score"] == 75
        assert len(message["samples"][0]["rogues"]) == 1
        assert message["samples"][0]["rogues"][0]["pid"] == 123
        assert message["samples"][0]["rogues"][0]["command"] == "heavy"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_broadcast_to_clients(short_tmp_path):
    """SocketServer.broadcast() should push ProcessSamples data to all connected clients."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read initial state (empty buffer)
        await asyncio.wait_for(reader.readline(), timeout=2.0)

        # Broadcast a sample with ProcessSamples
        samples = make_test_samples(
            max_score=80,
            process_count=150,
            elapsed_ms=1500,
            rogues=[
                make_test_process_score(pid=456, command="busy", score=80, cpu=90.0),
            ],
        )
        await server.broadcast(samples)

        # Read broadcast message
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        message = json.loads(data.decode())

        assert message["type"] == "sample"
        assert message["max_score"] == 80
        assert message["process_count"] == 150
        assert message["elapsed_ms"] == 1500
        assert len(message["rogues"]) == 1
        assert message["rogues"][0]["pid"] == 456
        assert message["rogues"][0]["command"] == "busy"
        assert message["rogues"][0]["cpu"] == 90.0

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_has_clients_property(short_tmp_path):
    """has_clients should reflect connected client count."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        assert not server.has_clients

        # Connect client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        # Client is registered before initial state is sent, so reading it confirms registration
        await asyncio.wait_for(reader.readline(), timeout=2.0)

        assert server.has_clients

        writer.close()
        await writer.wait_closed()
        # Poll for disconnect to be processed
        await wait_until(lambda: not server.has_clients)

        assert not server.has_clients
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_broadcast_no_clients(short_tmp_path):
    """broadcast() should be a no-op when no clients connected."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Should not raise when broadcasting with no clients
        samples = make_test_samples()
        await server.broadcast(samples)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_removes_stale_socket(short_tmp_path):
    """SocketServer should remove stale socket file on start."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    # Create a stale socket file
    socket_path.touch()
    assert socket_path.exists()

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    # Server should have replaced the stale file
    assert socket_path.exists()

    await server.stop()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_socket_server_multiple_clients(short_tmp_path):
    """SocketServer should broadcast to all connected clients."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect two clients
        reader1, writer1 = await asyncio.open_unix_connection(str(socket_path))
        reader2, writer2 = await asyncio.open_unix_connection(str(socket_path))

        # Drain initial state messages
        await asyncio.wait_for(reader1.readline(), timeout=2.0)
        await asyncio.wait_for(reader2.readline(), timeout=2.0)

        # Broadcast with ProcessSamples
        samples = make_test_samples(max_score=65)
        await server.broadcast(samples)

        # Both clients should receive
        data1 = await asyncio.wait_for(reader1.readline(), timeout=2.0)
        data2 = await asyncio.wait_for(reader2.readline(), timeout=2.0)

        msg1 = json.loads(data1.decode())
        msg2 = json.loads(data2.decode())

        assert msg1["max_score"] == 65
        assert msg2["max_score"] == 65

        writer1.close()
        writer2.close()
        await writer1.wait_closed()
        await writer2.wait_closed()
    finally:
        await server.stop()
