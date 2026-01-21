"""Tests for stress score calculation."""

from pause_monitor.stress import (
    IOBaselineManager,
    MemoryPressureLevel,
    StressBreakdown,
    calculate_stress,
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


def test_io_baseline_manager_initial_state():
    """IOBaselineManager starts with default baseline."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.baseline_fast == 10_000_000
    assert manager.learning is True


def test_io_baseline_manager_persisted():
    """IOBaselineManager uses persisted baseline if available."""
    manager = IOBaselineManager(persisted_baseline=50_000_000)
    assert manager.baseline_fast == 50_000_000
    assert manager.learning is False


def test_io_baseline_manager_update():
    """IOBaselineManager updates baseline with EMA."""
    manager = IOBaselineManager(persisted_baseline=10_000_000)
    manager.update(20_000_000)
    assert 10_900_000 < manager.baseline_fast < 11_100_000


def test_io_baseline_manager_learning_completes():
    """IOBaselineManager exits learning after enough samples."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.learning is True

    for _ in range(60):
        manager.update(10_000_000)

    assert manager.learning is False


def test_io_baseline_manager_spike_detection():
    """IOBaselineManager detects spikes correctly."""
    manager = IOBaselineManager(persisted_baseline=10_000_000)

    assert manager.is_spike(50_000_000) is False  # 5x, not spike
    assert manager.is_spike(110_000_000) is True  # 11x, spike


def test_io_baseline_manager_learning_spike_threshold():
    """During learning, only extreme absolute values are spikes."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.learning is True

    assert manager.is_spike(150_000_000) is False
    assert manager.is_spike(250_000_000) is True


def test_stress_breakdown_has_all_factors():
    """Verify StressBreakdown has all 7 factors."""
    breakdown = StressBreakdown(load=10, memory=15, thermal=0, latency=5, io=10, gpu=8, wakeups=5)
    assert breakdown.load == 10
    assert breakdown.memory == 15
    assert breakdown.thermal == 0
    assert breakdown.latency == 5
    assert breakdown.io == 10
    assert breakdown.gpu == 8
    assert breakdown.wakeups == 5


def test_stress_breakdown_total_includes_all_factors():
    """Verify total sums all 7 factors."""
    breakdown = StressBreakdown(
        load=40, memory=30, thermal=20, latency=30, io=20, gpu=20, wakeups=20
    )
    # 40+30+20+30+20+20+20 = 180, capped at 100
    assert breakdown.total == 100


def test_stress_breakdown_total_uncapped():
    """Verify total with small values."""
    breakdown = StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=0, gpu=3, wakeups=2)
    assert breakdown.total == 15


def test_stress_gpu_contribution():
    """GPU stress when sustained above 80%."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=85.0,
        wakeups_per_sec=100,
    )
    assert breakdown.gpu == 20  # Above 80% threshold


def test_stress_gpu_below_threshold():
    """No GPU stress below 80%."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=70.0,
        wakeups_per_sec=100,
    )
    assert breakdown.gpu == 0


def test_stress_wakeups_contribution():
    """Wakeups stress when above 1000/sec."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=0.0,
        wakeups_per_sec=1500,
    )
    assert breakdown.wakeups == 20  # Above 1000/sec threshold


def test_stress_wakeups_below_threshold():
    """No wakeups stress below 1000/sec."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=0.0,
        wakeups_per_sec=500,
    )
    assert breakdown.wakeups == 0
