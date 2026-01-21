"""Stress score calculation for pause-monitor."""

from dataclasses import dataclass


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
