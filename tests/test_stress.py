"""Tests for stress score calculation."""

from pause_monitor.stress import (
    MemoryPressureLevel,
    StressBreakdown,
    get_memory_pressure_fast,
)


def test_memory_pressure_returns_level():
    """get_memory_pressure_fast returns valid percentage."""
    level = get_memory_pressure_fast()
    assert 0 <= level <= 100


def test_memory_pressure_level_enum():
    """MemoryPressureLevel categorizes correctly."""
    assert MemoryPressureLevel.from_percent(80) == MemoryPressureLevel.NORMAL
    assert MemoryPressureLevel.from_percent(35) == MemoryPressureLevel.WARNING
    assert MemoryPressureLevel.from_percent(10) == MemoryPressureLevel.CRITICAL


def test_stress_breakdown_total():
    """StressBreakdown.total sums components."""
    breakdown = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    assert breakdown.total == 15


def test_stress_breakdown_total_capped():
    """StressBreakdown.total capped at 100."""
    breakdown = StressBreakdown(load=40, memory=30, thermal=20, latency=30, io=20, gpu=0, wakeups=0)
    assert breakdown.total == 100


def test_stress_breakdown_has_all_factors():
    """Verify StressBreakdown has all 8 factors."""
    breakdown = StressBreakdown(
        load=10, memory=15, thermal=0, latency=5, io=10, gpu=8, wakeups=5, pageins=12
    )
    assert breakdown.load == 10
    assert breakdown.memory == 15
    assert breakdown.thermal == 0
    assert breakdown.latency == 5
    assert breakdown.io == 10
    assert breakdown.gpu == 8
    assert breakdown.wakeups == 5
    assert breakdown.pageins == 12


def test_stress_breakdown_total_includes_all_factors():
    """Verify total sums all 8 factors."""
    breakdown = StressBreakdown(
        load=40, memory=30, thermal=20, latency=30, io=20, gpu=20, wakeups=20, pageins=30
    )
    # 40+30+20+30+20+20+20+30 = 210, capped at 100
    assert breakdown.total == 100


def test_stress_breakdown_total_uncapped():
    """Verify total with small values."""
    breakdown = StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=0, gpu=3, wakeups=2)
    assert breakdown.total == 15
