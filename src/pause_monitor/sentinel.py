"""Tier management for stress-based monitoring."""

from __future__ import annotations

import time
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """Monitoring tier levels."""

    SENTINEL = 1  # Normal: stress < elevated_threshold
    ELEVATED = 2  # Increased attention: elevated <= stress < critical
    CRITICAL = 3  # Maximum alert: stress >= critical_threshold


class TierAction(StrEnum):
    """Actions returned by TierManager on state transitions."""

    TIER2_ENTRY = "tier2_entry"
    TIER2_EXIT = "tier2_exit"
    TIER2_PEAK = "tier2_peak"
    TIER3_ENTRY = "tier3_entry"
    TIER3_EXIT = "tier3_exit"


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

    @property
    def tier2_entry_time(self) -> float | None:
        """Time (monotonic) when tier 2 was entered, or None if not in tier 2+."""
        return self._tier2_entry_time

    @property
    def tier3_entry_time(self) -> float | None:
        """Time (monotonic) when tier 3 was entered, or None if not in tier 3."""
        return self._tier3_entry_time

    def update(self, stress_total: int) -> TierAction | None:
        """Update tier state based on current stress.

        Returns TierAction if state change occurred, None otherwise.
        """
        now = time.monotonic()
        action: TierAction | None = None

        # Check for escalation first (immediate)
        if stress_total >= self.critical_threshold and self._current_tier < Tier.CRITICAL:
            self._current_tier = Tier.CRITICAL
            self._tier3_entry_time = now
            self._tier3_low_since = None
            # Track peak on entry to tier 3 as well
            if stress_total > self._peak_stress:
                self._peak_stress = stress_total
            return TierAction.TIER3_ENTRY

        if stress_total >= self.elevated_threshold and self._current_tier < Tier.ELEVATED:
            self._current_tier = Tier.ELEVATED
            self._tier2_entry_time = now
            self._tier2_low_since = None
            self._peak_stress = stress_total
            return TierAction.TIER2_ENTRY

        # Track peak during elevated states (after escalation checks)
        if self._current_tier >= Tier.ELEVATED and stress_total > self._peak_stress:
            self._peak_stress = stress_total
            # Only emit peak action for Tier 2 - Tier 3 is already critical.
            # Ring buffer triggers don't include "tier3_peak" by design.
            if self._current_tier == Tier.ELEVATED:
                action = TierAction.TIER2_PEAK

        # Check for de-escalation with hysteresis
        if self._current_tier == Tier.CRITICAL:
            if stress_total < self.critical_threshold:
                if self._tier3_low_since is None:
                    self._tier3_low_since = now
                elif now - self._tier3_low_since >= self.deescalation_delay:
                    self._current_tier = Tier.ELEVATED
                    self._tier3_entry_time = None
                    self._tier3_low_since = None
                    return TierAction.TIER3_EXIT
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
                    return TierAction.TIER2_EXIT
            else:
                self._tier2_low_since = None

        return action
