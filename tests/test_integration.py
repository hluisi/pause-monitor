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

from pause_monitor.collector import ProcessSamples, ProcessScore
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


def make_test_process_score(**kwargs) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    defaults = {
        "pid": 1,
        "command": "test_proc",
        "cpu": 50.0,
        "state": "running",
        "mem": 1024 * 1024,  # 1MB
        "cmprs": 0,
        "pageins": 10,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
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
        assert proc_in_broadcast["cpu"] == 85.1
        assert proc_in_broadcast["score"] == 75

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
