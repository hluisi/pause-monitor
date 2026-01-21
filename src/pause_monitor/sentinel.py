"""Stress sentinel with tiered monitoring.

Fast loop (100ms): load, memory, I/O via sysctl/IOKit
Slow loop (1s): GPU, wakeups, thermal via powermetrics
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import TYPE_CHECKING

import structlog

from pause_monitor.collector import get_core_count
from pause_monitor.stress import StressBreakdown, calculate_stress
from pause_monitor.sysctl import sysctl_int

if TYPE_CHECKING:
    from pause_monitor.ringbuffer import BufferContents, RingBuffer

log = structlog.get_logger()


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
            # Only emit peak action for Tier 2 - Tier 3 is already critical.
            # Ring buffer triggers don't include "tier3_peak" by design.
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


class Sentinel:
    """Continuous stress monitoring sentinel.

    Fast loop (100ms): load, memory via sysctl
    Slow loop (1s): GPU, wakeups, thermal via powermetrics cache
    """

    def __init__(
        self,
        buffer: RingBuffer,
        fast_interval_ms: int = 100,
        slow_interval_ms: int = 1000,
        elevated_threshold: int = 15,
        critical_threshold: int = 50,
    ) -> None:
        self.buffer = buffer
        self.fast_interval = fast_interval_ms / 1000.0
        self.slow_interval = slow_interval_ms / 1000.0
        self.tier_manager = TierManager(elevated_threshold, critical_threshold)

        self._running = False
        self._core_count = get_core_count()

        # Cached slow metrics (updated by slow loop)
        self._cached_gpu_pct: float | None = None
        self._cached_wakeups: int | None = None
        self._cached_throttled: bool | None = None

        # Callbacks (must be async)
        self.on_tier_change: Callable[[str, int], Awaitable[None]] | None = None
        self.on_pause_detected: Callable[[float, float, BufferContents], Awaitable[None]] | None = (
            None
        )

    def stop(self) -> None:
        """Signal sentinel to stop."""
        self._running = False

    async def start(self) -> None:
        """Run the sentinel loops."""
        self._running = True
        results = await asyncio.gather(
            self._fast_loop(),
            self._slow_loop(),
            return_exceptions=True,
        )
        # Surface any exceptions that occurred - per CLAUDE.md "Crashes Are Good"
        for result in results:
            if isinstance(result, Exception):
                log.error("loop_failed", error=str(result), exc_info=result)
                raise result

    async def _fast_loop(self) -> None:
        """100ms stress sampling loop."""
        last_time = time.monotonic()
        first_iteration = True

        while self._running:
            now = time.monotonic()
            elapsed = now - last_time
            last_time = now

            # First iteration has near-zero elapsed time, which would give invalid
            # latency_ratio. Use 1.0 (on-time) for the first sample.
            if first_iteration:
                latency_ratio = 1.0
                first_iteration = False
            else:
                latency_ratio = elapsed / self.fast_interval if self.fast_interval > 0 else 1.0

            metrics = collect_fast_metrics()
            stress = self._calculate_fast_stress(metrics, latency_ratio)

            self.buffer.push(stress, tier=self.tier_manager.current_tier)

            action = self.tier_manager.update(stress.total)
            if action:
                await self._handle_tier_action(action)

            if latency_ratio > 2.0:
                await self._handle_potential_pause(elapsed, self.fast_interval)

            await asyncio.sleep(self.fast_interval)

    async def _slow_loop(self) -> None:
        """1s loop for expensive metrics (GPU, wakeups)."""
        while self._running:
            # TODO: Collect GPU/wakeups/thermal via powermetrics
            await asyncio.sleep(self.slow_interval)

    def _calculate_fast_stress(self, metrics: dict, latency_ratio: float) -> StressBreakdown:
        """Calculate stress from fast metrics + cached slow metrics."""
        mem_pressure = metrics.get("memory_pressure")
        # Default to 100 (healthy) only if None; 0 is a valid critical value
        mem_available_pct = float(mem_pressure) if mem_pressure is not None else 100.0

        return calculate_stress(
            load_avg=metrics["load_avg"],
            core_count=self._core_count,
            mem_available_pct=mem_available_pct,
            throttled=self._cached_throttled,
            latency_ratio=latency_ratio,
            io_rate=0,
            io_baseline=0,
            gpu_pct=self._cached_gpu_pct,
            wakeups_per_sec=self._cached_wakeups,
        )

    async def _handle_tier_action(self, action: str) -> None:
        """Handle tier state changes."""
        log.info("tier_action", action=action, tier=self.tier_manager.current_tier)

        if action in ("tier2_entry", "tier2_peak", "tier3_entry"):
            self.buffer.snapshot_processes(trigger=action)

        if action == "tier2_exit":
            self.buffer.clear_snapshots()

        if self.on_tier_change:
            await self.on_tier_change(action, self.tier_manager.current_tier)

    async def _handle_potential_pause(self, actual: float, expected: float) -> None:
        """Handle potential pause detection."""
        log.warning(
            "potential_pause",
            actual=actual,
            expected=expected,
            ratio=actual / expected,
            tier=self.tier_manager.current_tier,
            peak_stress=self.tier_manager.peak_stress,
        )

        if self.on_pause_detected:
            await self.on_pause_detected(actual, expected, self.buffer.freeze())
