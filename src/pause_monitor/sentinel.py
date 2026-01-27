"""Tier management for score-based monitoring."""

from __future__ import annotations

import time
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """Monitoring tier levels."""

    SENTINEL = 1  # Normal: score < elevated_threshold
    ELEVATED = 2  # Increased attention: elevated <= score < critical
    CRITICAL = 3  # Maximum alert: score >= critical_threshold


class TierAction(StrEnum):
    """Actions returned by TierManager on state transitions."""

    TIER2_ENTRY = "tier2_entry"
    TIER2_EXIT = "tier2_exit"
    TIER2_PEAK = "tier2_peak"
    TIER3_ENTRY = "tier3_entry"
    TIER3_EXIT = "tier3_exit"


class TierManager:
    """Manages tier transitions based on score.

    Tier 1 (Sentinel): score < elevated_threshold
    Tier 2 (Elevated): elevated_threshold <= score < critical_threshold
    Tier 3 (Critical): score >= critical_threshold
    """

    def __init__(
        self,
        elevated_threshold: int,
        critical_threshold: int,
    ) -> None:
        self._elevated_threshold = elevated_threshold
        self._critical_threshold = critical_threshold

        self._current_tier = Tier.SENTINEL
        self._tier2_entry_time: float | None = None
        self._tier3_entry_time: float | None = None
        self._peak_score = 0

    @property
    def current_tier(self) -> int:
        """Current tier as integer (1, 2, or 3)."""
        return int(self._current_tier)

    @property
    def peak_score(self) -> int:
        """Peak score value seen during elevated states."""
        return self._peak_score

    @property
    def tier2_entry_time(self) -> float | None:
        """Time (monotonic) when tier 2 was entered, or None if not in tier 2+."""
        return self._tier2_entry_time

    @property
    def tier3_entry_time(self) -> float | None:
        """Time (monotonic) when tier 3 was entered, or None if not in tier 3."""
        return self._tier3_entry_time

    def update(self, score: int) -> TierAction | None:
        """Update tier state based on current score.

        Returns TierAction if state change occurred, None otherwise.
        """
        now = time.monotonic()

        # Determine what tier this score belongs to
        if score >= self._critical_threshold:
            new_tier = Tier.CRITICAL
        elif score >= self._elevated_threshold:
            new_tier = Tier.ELEVATED
        else:
            new_tier = Tier.SENTINEL

        # Track peak during elevated states
        is_new_peak = False
        if self._current_tier >= Tier.ELEVATED and score > self._peak_score:
            self._peak_score = score
            is_new_peak = True

        # No change
        if new_tier == self._current_tier:
            # Return TIER2_PEAK only when we actually set a new peak
            if self._current_tier == Tier.ELEVATED and is_new_peak:
                return TierAction.TIER2_PEAK
            return None

        # Determine action based on transition
        action: TierAction | None = None
        old_tier = self._current_tier
        self._current_tier = new_tier

        if new_tier == Tier.CRITICAL:
            self._tier3_entry_time = now
            if old_tier < Tier.ELEVATED:
                # Jumped straight to critical from sentinel
                self._tier2_entry_time = now
            self._peak_score = score
            action = TierAction.TIER3_ENTRY

        elif new_tier == Tier.ELEVATED:
            if old_tier == Tier.SENTINEL:
                # Entering elevated from sentinel
                self._tier2_entry_time = now
                self._peak_score = score
                action = TierAction.TIER2_ENTRY
            else:
                # Dropping from critical to elevated
                self._tier3_entry_time = None
                action = TierAction.TIER3_EXIT

        else:  # new_tier == Tier.SENTINEL
            # Dropping to sentinel
            self._tier2_entry_time = None
            self._tier3_entry_time = None
            self._peak_score = 0
            action = TierAction.TIER2_EXIT

        return action
