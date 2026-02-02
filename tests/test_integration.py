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

from rogue_hunter.collector import (
    ProcessSamples,
    ProcessScore,
)
from rogue_hunter.ringbuffer import RingBuffer
from rogue_hunter.socket_server import SocketServer

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
    cap_time = kwargs.pop("captured_at", time.time())
    pid = kwargs.pop("pid", 1)
    command = kwargs.pop("command", "test_proc")
    dominant_category = kwargs.pop("dominant_category", "blocking")
    dominant_metrics = kwargs.pop("dominant_metrics", ["cpu:50%"])

    cpu = kwargs.pop("cpu", 50.0)
    mem = kwargs.pop("mem", 1024 * 1024)
    pageins = kwargs.pop("pageins", 10)
    pageins_rate = kwargs.pop("pageins_rate", 0.0)
    faults = kwargs.pop("faults", 0)
    faults_rate = kwargs.pop("faults_rate", 0.0)
    disk_io = kwargs.pop("disk_io", 0)
    disk_io_rate = kwargs.pop("disk_io_rate", 0.0)
    csw = kwargs.pop("csw", 100)
    csw_rate = kwargs.pop("csw_rate", 0.0)
    syscalls = kwargs.pop("syscalls", 50)
    syscalls_rate = kwargs.pop("syscalls_rate", 0.0)
    threads = kwargs.pop("threads", 4)
    mach_msgs = kwargs.pop("mach_msgs", 0)
    mach_msgs_rate = kwargs.pop("mach_msgs_rate", 0.0)
    instructions = kwargs.pop("instructions", 0)
    cycles = kwargs.pop("cycles", 0)
    ipc = kwargs.pop("ipc", 0.0)
    energy = kwargs.pop("energy", 0)
    energy_rate = kwargs.pop("energy_rate", 0.0)
    wakeups = kwargs.pop("wakeups", 0)
    wakeups_rate = kwargs.pop("wakeups_rate", 0.0)
    runnable_time = kwargs.pop("runnable_time", 0)
    runnable_time_rate = kwargs.pop("runnable_time_rate", 0.0)
    qos_interactive = kwargs.pop("qos_interactive", 0)
    qos_interactive_rate = kwargs.pop("qos_interactive_rate", 0.0)
    gpu_time = kwargs.pop("gpu_time", 0)
    gpu_time_rate = kwargs.pop("gpu_time_rate", 0.0)
    zombie_children = kwargs.pop("zombie_children", 0)
    priority = kwargs.pop("priority", 31)
    score = kwargs.pop("score", 50)
    blocking_score = kwargs.pop("blocking_score", score * 0.4)
    contention_score = kwargs.pop("contention_score", score * 0.3)
    pressure_score = kwargs.pop("pressure_score", score * 0.2)
    efficiency_score = kwargs.pop("efficiency_score", score * 0.1)

    state = kwargs.pop("state", "running")
    band = kwargs.pop("band", "elevated")

    return ProcessScore(
        pid=pid,
        command=command,
        captured_at=cap_time,
        cpu=cpu,
        mem=mem,
        mem_peak=mem,
        pageins=pageins,
        pageins_rate=pageins_rate,
        faults=faults,
        faults_rate=faults_rate,
        disk_io=disk_io,
        disk_io_rate=disk_io_rate,
        csw=csw,
        csw_rate=csw_rate,
        syscalls=syscalls,
        syscalls_rate=syscalls_rate,
        threads=threads,
        mach_msgs=mach_msgs,
        mach_msgs_rate=mach_msgs_rate,
        instructions=instructions,
        cycles=cycles,
        ipc=ipc,
        energy=energy,
        energy_rate=energy_rate,
        wakeups=wakeups,
        wakeups_rate=wakeups_rate,
        runnable_time=runnable_time,
        runnable_time_rate=runnable_time_rate,
        qos_interactive=qos_interactive,
        qos_interactive_rate=qos_interactive_rate,
        gpu_time=gpu_time,
        gpu_time_rate=gpu_time_rate,
        zombie_children=zombie_children,
        state=state,
        priority=priority,
        score=score,
        band=band,
        blocking_score=blocking_score,
        contention_score=contention_score,
        pressure_score=pressure_score,
        efficiency_score=efficiency_score,
        dominant_category=dominant_category,
        dominant_metrics=(
            list(dominant_metrics) if isinstance(dominant_metrics, frozenset) else dominant_metrics
        ),
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
        # Fields are now plain values
        assert proc_in_broadcast["cpu"] == 85.1
        assert proc_in_broadcast["score"] == 75

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
