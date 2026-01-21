"""Stress score calculation for pause-monitor."""

from dataclasses import dataclass


@dataclass
class StressBreakdown:
    """Per-factor stress scores.

    This is the CANONICAL definition - storage.py imports from here.
    """

    load: int      # 0-40: load/cores ratio
    memory: int    # 0-30: memory pressure
    thermal: int   # 0-20: throttling active
    latency: int   # 0-30: self-latency
    io: int        # 0-20: disk I/O spike

    @property
    def total(self) -> int:
        """Combined stress score, capped at 100."""
        return min(100, self.load + self.memory + self.thermal + self.latency + self.io)
