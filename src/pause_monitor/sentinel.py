"""Stress sentinel with tiered monitoring.

Fast loop (100ms): load, memory, I/O via sysctl/IOKit
Slow loop (1s): GPU, wakeups, thermal via powermetrics
"""

import os
import time
from enum import IntEnum

from pause_monitor.sysctl import sysctl_int


class Tier(IntEnum):
    """Monitoring tier levels."""

    SENTINEL = 1  # Normal: stress < elevated_threshold
    ELEVATED = 2  # Increased attention: elevated <= stress < critical
    CRITICAL = 3  # Maximum alert: stress >= critical_threshold


class TierManager:
    """Manages tier transitions with hysteresis.

    Tier 1 (Sentinel): stress < elevated_threshold
    Tier 2 (Elevated): elevated_threshold <= stress < critical_threshold
    Tier 3 (Critical): stress >= critical_threshold

    De-escalation requires stress below threshold for 5 seconds.
    """

    def __init__(
        self,
        elevated_threshold: int = 15,
        critical_threshold: int = 50,
        deescalation_delay: float = 5.0,
    ) -> None:
        self.elevated_threshold = elevated_threshold
        self.critical_threshold = critical_threshold
        self.deescalation_delay = deescalation_delay

        self._current_tier = Tier.SENTINEL
        self._tier2_entry_time: float | None = None
        self._tier3_entry_time: float | None = None
        self._tier2_low_since: float | None = None
        self._tier3_low_since: float | None = None
        self._peak_stress = 0

    @property
    def current_tier(self) -> int:
        """Current tier as integer (1, 2, or 3)."""
        return int(self._current_tier)

    @property
    def peak_stress(self) -> int:
        """Peak stress value seen during elevated states."""
        return self._peak_stress

    def update(self, stress_total: int) -> str | None:
        """Update tier state based on current stress.

        Returns action string if state change occurred:
        - "tier2_entry", "tier2_exit"
        - "tier3_entry", "tier3_exit"
        - "tier2_peak" if new peak reached in tier 2
        - None if no action needed
        """
        now = time.monotonic()
        action: str | None = None

        # Check for escalation first (immediate)
        if stress_total >= self.critical_threshold and self._current_tier < Tier.CRITICAL:
            self._current_tier = Tier.CRITICAL
            self._tier3_entry_time = now
            self._tier3_low_since = None
            # Track peak on entry to tier 3 as well
            if stress_total > self._peak_stress:
                self._peak_stress = stress_total
            return "tier3_entry"

        if stress_total >= self.elevated_threshold and self._current_tier < Tier.ELEVATED:
            self._current_tier = Tier.ELEVATED
            self._tier2_entry_time = now
            self._tier2_low_since = None
            self._peak_stress = stress_total
            return "tier2_entry"

        # Track peak during elevated states (after escalation checks)
        if self._current_tier >= Tier.ELEVATED and stress_total > self._peak_stress:
            self._peak_stress = stress_total
            if self._current_tier == Tier.ELEVATED:
                action = "tier2_peak"

        # Check for de-escalation with hysteresis
        if self._current_tier == Tier.CRITICAL:
            if stress_total < self.critical_threshold:
                if self._tier3_low_since is None:
                    self._tier3_low_since = now
                elif now - self._tier3_low_since >= self.deescalation_delay:
                    self._current_tier = Tier.ELEVATED
                    self._tier3_entry_time = None
                    self._tier3_low_since = None
                    return "tier3_exit"
            else:
                self._tier3_low_since = None

        if self._current_tier == Tier.ELEVATED:
            if stress_total < self.elevated_threshold:
                if self._tier2_low_since is None:
                    self._tier2_low_since = now
                elif now - self._tier2_low_since >= self.deescalation_delay:
                    self._current_tier = Tier.SENTINEL
                    self._tier2_entry_time = None
                    self._tier2_low_since = None
                    self._peak_stress = 0
                    return "tier2_exit"
            else:
                self._tier2_low_since = None

        return action


def collect_fast_metrics() -> dict:
    """Collect fast-path metrics (~20us).

    Uses sysctl and os.getloadavg() - no subprocess calls.
    """
    load_avg = os.getloadavg()[0]  # 1-minute average
    memory_pressure = sysctl_int("kern.memorystatus_level")  # 0-100
    page_free_count = sysctl_int("vm.page_free_count")

    return {
        "load_avg": load_avg,
        "memory_pressure": memory_pressure,
        "page_free_count": page_free_count,
    }
