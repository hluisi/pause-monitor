"""Stress score calculation for pause-monitor."""

import ctypes
from dataclasses import dataclass
from enum import Enum

import structlog

log = structlog.get_logger()


class MemoryPressureLevel(Enum):
    """Memory pressure categories."""

    NORMAL = "normal"  # >50% available
    WARNING = "warning"  # 20-50% available
    CRITICAL = "critical"  # <20% available

    @classmethod
    def from_percent(cls, available_pct: int) -> "MemoryPressureLevel":
        """Categorize memory pressure from availability percentage."""
        if available_pct > 50:
            return cls.NORMAL
        elif available_pct >= 20:
            return cls.WARNING
        else:
            return cls.CRITICAL


def get_memory_pressure_fast() -> int:
    """Get memory pressure level via sysctl (no subprocess).

    Returns:
        Percentage of memory "free" (0-100). Higher = more available.
    """
    libc = ctypes.CDLL("/usr/lib/libc.dylib")
    size = ctypes.c_size_t(4)
    level = ctypes.c_int()

    result = libc.sysctlbyname(
        b"kern.memorystatus_level",
        ctypes.byref(level),
        ctypes.byref(size),
        None,
        0,
    )

    if result != 0:
        return 50  # Fallback: assume moderate pressure

    return level.value


@dataclass
class StressBreakdown:
    """Per-factor stress scores.

    This is the CANONICAL definition - storage.py imports from here.
    """

    load: int  # 0-40: load/cores ratio
    memory: int  # 0-30: memory pressure
    thermal: int  # 0-20: throttling active
    latency: int  # 0-30: self-latency
    io: int  # 0-20: disk I/O spike

    @property
    def total(self) -> int:
        """Combined stress score, capped at 100."""
        return min(100, self.load + self.memory + self.thermal + self.latency + self.io)


class IOBaselineManager:
    """Manage I/O baseline with learning period awareness."""

    LEARNING_SAMPLES = 60  # ~1 minute at 1s sampling
    DEFAULT_BASELINE = 10_000_000  # 10 MB/s

    def __init__(self, persisted_baseline: float | None):
        self.baseline_fast = persisted_baseline or self.DEFAULT_BASELINE
        self.baseline_slow = persisted_baseline or self.DEFAULT_BASELINE
        self.samples_seen = 0 if persisted_baseline is None else self.LEARNING_SAMPLES
        self.learning = self.samples_seen < self.LEARNING_SAMPLES

    def update(self, io_rate: float) -> None:
        """Update baselines with new I/O rate observation."""
        self.samples_seen += 1

        if self.learning:
            alpha_fast = 0.3
            alpha_slow = 0.1

            if self.samples_seen >= self.LEARNING_SAMPLES:
                self.learning = False
                log.info(
                    "io_baseline_learning_complete",
                    baseline_fast=self.baseline_fast,
                    baseline_slow=self.baseline_slow,
                )
        else:
            alpha_fast = 0.1
            alpha_slow = 0.001

        self.baseline_fast = alpha_fast * io_rate + (1 - alpha_fast) * self.baseline_fast
        self.baseline_slow = alpha_slow * io_rate + (1 - alpha_slow) * self.baseline_slow

    def is_spike(self, io_rate: float) -> bool:
        """Check if current I/O rate is a spike relative to baseline."""
        if self.learning:
            return io_rate > 200_000_000  # 200 MB/s absolute during learning

        return io_rate > self.baseline_fast * 10


def calculate_stress(
    load_avg: float,
    core_count: int,
    mem_available_pct: float,
    throttled: bool | None,
    latency_ratio: float,
    io_rate: int,
    io_baseline: int,
) -> StressBreakdown:
    """Calculate stress score from current system metrics.

    Args:
        load_avg: 1-minute load average
        core_count: Number of CPU cores
        mem_available_pct: Percentage of memory available (0-100)
        throttled: True if thermal throttling active, None if unknown
        latency_ratio: actual_interval / expected_interval
        io_rate: Current I/O bytes/sec (read + write)
        io_baseline: Baseline I/O bytes/sec (EMA)

    Returns:
        StressBreakdown with per-factor and total scores
    """
    # Load average relative to cores (max 40 points)
    load_ratio = load_avg / core_count if core_count > 0 else 0
    load_score = min(40, max(0, int((load_ratio - 1.0) * 20)))

    # Memory pressure (max 30 points)
    mem_score = min(30, max(0, int((20 - mem_available_pct) * 1.5)))

    # Thermal throttling (20 points if active)
    thermal_score = 20 if throttled else 0

    # Self-latency (max 30 points, only if ratio > 1.5)
    if latency_ratio > 1.5:
        latency_score = min(30, max(0, int((latency_ratio - 1.0) * 20)))
    else:
        latency_score = 0

    # Disk I/O spike (20 points if detected)
    spike_detected = io_baseline > 0 and io_rate > io_baseline * 10
    sustained_high = io_rate > 100_000_000  # 100 MB/s
    io_score = 20 if (spike_detected or sustained_high) else 0

    return StressBreakdown(
        load=load_score,
        memory=mem_score,
        thermal=thermal_score,
        latency=latency_score,
        io=io_score,
    )
