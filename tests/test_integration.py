"""Integration tests for per-process scoring feature.

Tests the end-to-end data flow:
  Collector -> ProcessSamples -> RingBuffer -> Storage -> Socket

These tests use mocks where appropriate to avoid running actual system commands.
"""

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

# --- Test Fixtures ---


@pytest.fixture
def short_tmp_path():
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_int_") as tmpdir:
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
    command = kwargs.pop("command", "test_proc")
    categories = kwargs.pop("categories", ["cpu"])

    # Handle MetricValue fields - convert simple values to MetricValue
    cpu = kwargs.pop("cpu", 50.0)
    mem = kwargs.pop("mem", 1024 * 1024)
    pageins = kwargs.pop("pageins", 10)
    faults = kwargs.pop("faults", 0)
    disk_io = kwargs.pop("disk_io", 0)
    disk_io_rate = kwargs.pop("disk_io_rate", 0.0)
    csw = kwargs.pop("csw", 100)
    syscalls = kwargs.pop("syscalls", 50)
    threads = kwargs.pop("threads", 4)
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


# --- Integration Test: ProcessSamples -> Storage -> Retrieval ---


# --- Integration Test: ProcessSamples -> RingBuffer -> Freeze ---


def test_ring_buffer_stores_process_samples():
    """RingBuffer should store ProcessSamples and maintain order."""
    buffer = RingBuffer(max_samples=10)

    # Push samples with increasing scores
    for i in range(5):
        samples = make_test_samples(
            max_score=10 + i * 10,
            rogues=[make_test_process_score(pid=i, score=10 + i * 10)],
        )
        buffer.push(samples)

    assert len(buffer) == 5

    ring_samples = buffer.samples
    scores = [rs.samples.max_score for rs in ring_samples]
    assert scores == [10, 20, 30, 40, 50]


def test_ring_buffer_freeze_captures_state():
    """freeze() should return immutable snapshot of ProcessSamples."""
    buffer = RingBuffer(max_samples=10)

    samples = make_test_samples(
        max_score=75,
        rogues=[
            make_test_process_score(pid=1, command="proc1", score=75),
            make_test_process_score(pid=2, command="proc2", score=60),
        ],
    )
    buffer.push(samples)

    frozen = buffer.freeze()

    # Verify frozen state
    assert len(frozen.samples) == 1
    assert frozen.samples[0].samples.max_score == 75
    assert len(frozen.samples[0].samples.rogues) == 2

    # Verify immutability (tuple)
    assert isinstance(frozen.samples, tuple)


def test_ring_buffer_respects_max_samples():
    """RingBuffer should evict oldest samples when full."""
    buffer = RingBuffer(max_samples=3)

    # Push 5 samples
    for i in range(5):
        samples = make_test_samples(
            max_score=i * 10,
            rogues=[make_test_process_score(pid=i, score=i * 10)],
        )
        buffer.push(samples)

    assert len(buffer) == 3

    # Should have the last 3 samples (indices 2, 3, 4)
    ring_samples = buffer.samples
    scores = [rs.samples.max_score for rs in ring_samples]
    assert scores == [20, 30, 40]


# --- Integration Test: Full Cycle with Socket ---


@pytest.mark.asyncio
async def test_full_collection_to_socket_cycle(short_tmp_path):
    """Test complete buffer -> socket broadcast cycle."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=30)

    # Create sample data
    samples = make_test_samples(
        max_score=75,
        process_count=100,
        rogues=[
            make_test_process_score(pid=123, command="test_proc", score=75, cpu=85.1),
        ],
    )

    # Start socket server
    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect a client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Wait for client to be registered
        deadline = asyncio.get_event_loop().time() + 1.0
        while not server.has_clients:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Client not registered")
            await asyncio.sleep(0.01)

        # Read initial_state message first
        init_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        init_msg = json.loads(init_data.decode())
        assert init_msg["type"] == "initial_state"

        # Push to ring buffer
        buffer.push(samples)

        # Broadcast to clients
        await server.broadcast(samples)

        # Verify client receives broadcast
        broadcast_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        broadcast_msg = json.loads(broadcast_data.decode())

        assert broadcast_msg["type"] == "sample"
        assert broadcast_msg["max_score"] == samples.max_score
        assert broadcast_msg["process_count"] == samples.process_count
        assert len(broadcast_msg["rogues"]) == len(samples.rogues)

        # Verify rogue data in broadcast
        proc_in_broadcast = next(
            (r for r in broadcast_msg["rogues"] if r["command"] == "test_proc"), None
        )
        assert proc_in_broadcast is not None
        # MetricValue fields are serialized as {"current", "low", "high"} dicts
        assert proc_in_broadcast["cpu"]["current"] == 85.1
        assert proc_in_broadcast["score"]["current"] == 75

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
