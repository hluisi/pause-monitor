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
