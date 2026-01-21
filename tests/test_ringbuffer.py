# tests/test_ringbuffer.py
"""Tests for ring buffer module."""

from datetime import datetime

from pause_monitor.ringbuffer import RingSample
from pause_monitor.stress import StressBreakdown


def test_ring_sample_creation():
    """RingSample stores timestamp, stress breakdown, and tier."""
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    sample = RingSample(
        timestamp=datetime.now(),
        stress=stress,
        tier=1,
    )
    assert sample.tier == 1
    assert sample.stress.total == 15


def test_process_info_creation():
    """ProcessInfo stores process details."""
    from pause_monitor.ringbuffer import ProcessInfo

    info = ProcessInfo(pid=1234, name="Chrome", cpu_pct=45.5, memory_mb=2048.0)
    assert info.pid == 1234
    assert info.name == "Chrome"
    assert info.cpu_pct == 45.5
    assert info.memory_mb == 2048.0


def test_process_snapshot_creation():
    """ProcessSnapshot stores top processes with trigger reason."""
    from pause_monitor.ringbuffer import ProcessInfo, ProcessSnapshot

    by_cpu = [ProcessInfo(pid=1, name="Proc1", cpu_pct=50.0, memory_mb=100.0)]
    by_memory = [ProcessInfo(pid=2, name="Proc2", cpu_pct=10.0, memory_mb=2000.0)]

    snapshot = ProcessSnapshot(
        timestamp=datetime.now(),
        trigger="tier2_entry",
        by_cpu=by_cpu,
        by_memory=by_memory,
    )
    assert snapshot.trigger == "tier2_entry"
    assert len(snapshot.by_cpu) == 1
    assert len(snapshot.by_memory) == 1


def test_ring_buffer_push():
    """RingBuffer stores samples up to max size."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=3)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)

    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)

    assert len(buffer.samples) == 3


def test_ring_buffer_evicts_oldest():
    """RingBuffer evicts oldest when full."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=3)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)

    buffer.push(stress, tier=1)  # Will be evicted
    first_time = buffer.samples[0].timestamp

    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)  # Evicts first

    assert len(buffer.samples) == 3
    assert buffer.samples[0].timestamp != first_time


def test_ring_buffer_snapshot_processes():
    """RingBuffer captures process snapshots."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=300)

    buffer.snapshot_processes(trigger="tier2_entry")

    assert len(buffer.snapshots) == 1
    assert buffer.snapshots[0].trigger == "tier2_entry"


def test_ring_buffer_freeze():
    """freeze() returns immutable copy of buffer contents."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=300)
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.snapshot_processes(trigger="test")

    frozen = buffer.freeze()

    # Modifying original doesn't affect frozen
    buffer.push(stress, tier=2)
    assert len(frozen.samples) == 1
    assert len(buffer.samples) == 2


def test_ring_buffer_clear_snapshots():
    """clear_snapshots() removes process snapshots but keeps samples."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=300)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.snapshot_processes(trigger="test")

    buffer.clear_snapshots()

    assert len(buffer.samples) == 1
    assert len(buffer.snapshots) == 0


def test_ring_buffer_freeze_empty():
    """freeze() works on empty buffer."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=10)
    frozen = buffer.freeze()
    assert len(frozen.samples) == 0
    assert len(frozen.snapshots) == 0


def test_ring_buffer_size_one():
    """RingBuffer with max_samples=1 only keeps last sample."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=1)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=2)
    assert len(buffer.samples) == 1
    assert buffer.samples[0].tier == 2
