# tests/test_socket_server.py
"""Tests for Unix socket server module."""

import asyncio
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.collector import (
    MetricValue,
    MetricValueStr,
    ProcessSamples,
    ProcessScore,
)
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


def _metric(val: float | int) -> MetricValue:
    """Create MetricValue with same value for current/low/high."""
    return MetricValue(current=val, low=val, high=val)


def _metric_str(val: str) -> MetricValueStr:
    """Create MetricValueStr with same value for current/low/high."""
    return MetricValueStr(current=val, low=val, high=val)


def make_test_process_score(**kwargs) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    cap_time = kwargs.pop("captured_at", time.time())
    pid = kwargs.pop("pid", 1)
    command = kwargs.pop("command", "test")
    categories = kwargs.pop("categories", ["cpu"])

    # Handle MetricValue fields - convert simple values to MetricValue
    cpu = kwargs.pop("cpu", 50.0)
    mem = kwargs.pop("mem", 1000)
    pageins = kwargs.pop("pageins", 0)
    faults = kwargs.pop("faults", 0)
    disk_io = kwargs.pop("disk_io", 0)
    disk_io_rate = kwargs.pop("disk_io_rate", 0.0)
    csw = kwargs.pop("csw", 0)
    syscalls = kwargs.pop("syscalls", 0)
    threads = kwargs.pop("threads", 1)
    mach_msgs = kwargs.pop("mach_msgs", 0)
    instructions = kwargs.pop("instructions", 0)
    cycles = kwargs.pop("cycles", 0)
    ipc = kwargs.pop("ipc", 0.0)
    energy = kwargs.pop("energy", 0)
    energy_rate = kwargs.pop("energy_rate", 0.0)
    wakeups = kwargs.pop("wakeups", 0)
    priority = kwargs.pop("priority", 31)
    score = kwargs.pop("score", 50)

    # MetricValueStr fields
    state = kwargs.pop("state", "running")
    band = kwargs.pop("band", "elevated")

    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=cap_time,
        cpu=_metric(cpu),
        mem=_metric(mem),
        mem_peak=mem,
        pageins=_metric(pageins),
        faults=_metric(faults),
        disk_io=_metric(disk_io),
        disk_io_rate=_metric(disk_io_rate),
        csw=_metric(csw),
        syscalls=_metric(syscalls),
        threads=_metric(threads),
        mach_msgs=_metric(mach_msgs),
        instructions=_metric(instructions),
        cycles=_metric(cycles),
        ipc=_metric(ipc),
        energy=_metric(energy),
        energy_rate=_metric(energy_rate),
        wakeups=_metric(wakeups),
        state=_metric_str(state),
        priority=_metric(priority),
        score=_metric(score),
        band=_metric_str(band),
        categories=list(categories) if isinstance(categories, frozenset) else categories,
    )


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
async def test_socket_server_broadcast_to_clients(short_tmp_path):
    """SocketServer.broadcast() should push ProcessSamples data to all connected clients."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Wait for client to be registered
        await wait_until(lambda: server.has_clients)

        # Read and verify initial_state message
        init_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        init_msg = json.loads(init_data.decode())
        assert init_msg["type"] == "initial_state"

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
        # MetricValue fields are serialized as {"current", "low", "high"} dicts
        assert message["rogues"][0]["cpu"]["current"] == 90.0

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

        # Wait for client to be registered
        await wait_until(lambda: server.has_clients)

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

        # Wait for both clients to be registered
        await wait_until(lambda: len(server._clients) == 2)

        # Read initial_state messages from both clients
        init1 = await asyncio.wait_for(reader1.readline(), timeout=2.0)
        init2 = await asyncio.wait_for(reader2.readline(), timeout=2.0)
        assert json.loads(init1.decode())["type"] == "initial_state"
        assert json.loads(init2.decode())["type"] == "initial_state"

        # Broadcast with ProcessSamples
        samples = make_test_samples(max_score=65)
        await server.broadcast(samples)

        # Both clients should receive the sample
        data1 = await asyncio.wait_for(reader1.readline(), timeout=2.0)
        data2 = await asyncio.wait_for(reader2.readline(), timeout=2.0)

        msg1 = json.loads(data1.decode())
        msg2 = json.loads(data2.decode())

        assert msg1["type"] == "sample"
        assert msg1["max_score"] == 65
        assert msg2["type"] == "sample"
        assert msg2["max_score"] == 65

        writer1.close()
        writer2.close()
        await writer1.wait_closed()
        await writer2.wait_closed()
    finally:
        await server.stop()
