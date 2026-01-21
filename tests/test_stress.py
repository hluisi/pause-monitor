"""Tests for stress score calculation."""

from pause_monitor.stress import StressBreakdown, calculate_stress


def test_stress_breakdown_total():
    """StressBreakdown.total sums components."""
    breakdown = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
    assert breakdown.total == 15


def test_stress_breakdown_total_capped():
    """StressBreakdown.total capped at 100."""
    breakdown = StressBreakdown(load=40, memory=30, thermal=20, latency=30, io=20)
    assert breakdown.total == 100


def test_stress_zero_when_idle():
    """Idle system has zero stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.total == 0


def test_stress_load_contribution():
    """Load above cores contributes to stress."""
    breakdown = calculate_stress(
        load_avg=16.0,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.load == 20
    assert breakdown.total == 20


def test_stress_load_capped_at_40():
    """Load contribution capped at 40."""
    breakdown = calculate_stress(
        load_avg=40.0,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.load == 40


def test_stress_memory_contribution():
    """Low available memory contributes to stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=10.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.memory == 15


def test_stress_thermal_contribution():
    """Thermal throttling adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=True,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.thermal == 20


def test_stress_latency_contribution():
    """High latency ratio contributes to stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=2.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.latency == 20


def test_stress_latency_only_above_threshold():
    """Latency only contributes if ratio > 1.5."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.4,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.latency == 0


def test_stress_io_spike_contribution():
    """I/O spike (10x baseline) adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=150_000_000,
        io_baseline=10_000_000,
    )
    assert breakdown.io == 20


def test_stress_io_sustained_high():
    """Sustained high I/O (>100 MB/s) adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=150_000_000,
        io_baseline=100_000_000,
    )
    assert breakdown.io == 20
