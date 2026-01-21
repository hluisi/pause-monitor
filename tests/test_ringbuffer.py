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
