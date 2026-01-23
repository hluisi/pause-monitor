"""Stress score calculation for pause-monitor."""

from dataclasses import dataclass
from enum import Enum


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
    from pause_monitor.sysctl import sysctl_int

    result = sysctl_int("kern.memorystatus_level")
    if result is None:
        return 50  # Fallback: assume moderate pressure
    return result


@dataclass
class StressBreakdown:
    """Per-factor stress scores (8 factors).

    This is the CANONICAL definition - storage.py imports from here.
    Note: 8 factors as of redesign (pageins added for pause detection).
    """

    load: int  # 0-30: load/cores ratio
    memory: int  # 0-30: memory pressure
    thermal: int  # 0-10: throttling active
    latency: int  # 0-20: self-latency
    io: int  # 0-10: disk I/O activity
    gpu: int  # 0-20: GPU usage sustained high
    wakeups: int  # 0-10: idle wakeups sustained high
    pageins: int = 0  # 0-30: swap activity (CRITICAL for pause detection)

    @property
    def total(self) -> int:
        """Combined stress score, capped at 100."""
        raw = (
            self.load
            + self.memory
            + self.thermal
            + self.latency
            + self.io
            + self.gpu
            + self.wakeups
            + self.pageins
        )
        return min(100, raw)
