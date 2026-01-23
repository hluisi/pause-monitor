# tests/test_socket_server.py
"""Tests for Unix socket server module."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from pause_monitor.collector import PowermetricsResult
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.socket_server import SocketServer
from pause_monitor.stress import StressBreakdown


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


def make_test_metrics(**kwargs) -> PowermetricsResult:
    """Create PowermetricsResult with sensible defaults for testing."""
    defaults = {
        "elapsed_ns": 100_000_000,
        "throttled": False,
        "cpu_power": 5.0,
        "gpu_pct": 10.0,
        "gpu_power": 1.0,
        "io_read_per_s": 1000.0,
        "io_write_per_s": 500.0,
        "wakeups_per_s": 50.0,
        "pageins_per_s": 0.0,
        "top_cpu_processes": [],
        "top_pagein_processes": [],
    }
    defaults.update(kwargs)
    return PowermetricsResult(**defaults)


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

    # Add samples (Phase 1: push requires metrics)
    metrics = make_test_metrics(wakeups_per_s=100.0)
    stress = StressBreakdown(
        load=10, memory=5, thermal=0, latency=2, io=0, gpu=15, wakeups=3, pageins=0
    )
    buffer.push(metrics, stress, tier=1)

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
        assert "tier" in message
        assert len(message["samples"]) == 1
        assert message["samples"][0]["stress"]["load"] == 10
        assert message["samples"][0]["stress"]["gpu"] == 15

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_broadcast_to_clients(short_tmp_path):
    """SocketServer.broadcast() should push data to all connected clients."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read initial state (empty buffer)
        await asyncio.wait_for(reader.readline(), timeout=2.0)

        # Broadcast a sample
        metrics = make_test_metrics(gpu_pct=75.0)
        stress = StressBreakdown(
            load=20, memory=10, thermal=5, latency=0, io=3, gpu=18, wakeups=2, pageins=0
        )
        await server.broadcast(metrics, stress, tier=2)

        # Read broadcast message
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        message = json.loads(data.decode())

        assert message["type"] == "sample"
        assert message["tier"] == 2
        assert message["stress"]["load"] == 20
        assert message["stress"]["gpu"] == 18
        assert message["metrics"]["gpu_pct"] == 75.0

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
        metrics = make_test_metrics()
        stress = StressBreakdown(
            load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0, pageins=0
        )
        await server.broadcast(metrics, stress, tier=1)
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

        # Broadcast
        metrics = make_test_metrics()
        stress = StressBreakdown(
            load=15, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0, pageins=0
        )
        await server.broadcast(metrics, stress, tier=1)

        # Both clients should receive
        data1 = await asyncio.wait_for(reader1.readline(), timeout=2.0)
        data2 = await asyncio.wait_for(reader2.readline(), timeout=2.0)

        msg1 = json.loads(data1.decode())
        msg2 = json.loads(data2.decode())

        assert msg1["stress"]["load"] == 15
        assert msg2["stress"]["load"] == 15

        writer1.close()
        writer2.close()
        await writer1.wait_closed()
        await writer2.wait_closed()
    finally:
        await server.stop()
