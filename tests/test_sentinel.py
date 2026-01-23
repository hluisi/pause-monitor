"""Tests for sentinel module - fast stress monitoring."""

import asyncio
from unittest.mock import patch

import pytest

from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.sentinel import Sentinel, TierManager, collect_fast_metrics
from pause_monitor.sysctl import sysctl_int


def test_collect_fast_metrics_returns_dict():
    """Fast metrics collection returns required fields."""
    metrics = collect_fast_metrics()

    assert "load_avg" in metrics
    assert "memory_pressure" in metrics
    assert "page_free_count" in metrics
    assert isinstance(metrics["load_avg"], float)


def test_sysctl_int_returns_none_for_invalid():
    """Invalid sysctl names return None."""
    assert sysctl_int("this.does.not.exist") is None


def test_collect_fast_metrics_value_ranges():
    """Fast metrics values are in expected ranges."""
    metrics = collect_fast_metrics()

    assert metrics["load_avg"] >= 0.0  # Load can't be negative

    # memory_pressure is 0-100 or None
    mp = metrics["memory_pressure"]
    assert mp is None or (0 <= mp <= 100)

    # page_free_count is positive or None
    pfc = metrics["page_free_count"]
    assert pfc is None or pfc >= 0


# === TierManager Tests ===


def test_tier_manager_starts_at_tier1():
    """TierManager starts in Tier 1 (Sentinel)."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    assert manager.current_tier == 1


def test_tier_manager_escalates_to_tier2():
    """Stress >= 15 triggers escalation to Tier 2."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    action = manager.update(stress_total=20)

    assert manager.current_tier == 2
    assert action == "tier2_entry"


def test_tier_manager_escalates_to_tier3():
    """Stress >= 50 triggers escalation to Tier 3."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Enter tier 2 first

    action = manager.update(stress_total=55)

    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_direct_escalation_to_tier3():
    """Stress >= 50 triggers direct escalation from Tier 1 to Tier 3."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    action = manager.update(stress_total=55)

    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_deescalates_with_hysteresis():
    """Tier 2 requires 5 seconds below threshold to de-escalate."""
    import time

    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Enter tier 2

    # Still in tier 2 even though stress dropped
    action = manager.update(stress_total=10)
    assert manager.current_tier == 2
    assert action is None

    # Simulate time passing (manipulate internal state for testing)
    manager._tier2_low_since = time.monotonic() - 6.0
    action = manager.update(stress_total=10)

    assert manager.current_tier == 1
    assert action == "tier2_exit"


def test_tier_manager_tier3_deescalates_with_hysteresis():
    """Tier 3 requires 5 seconds below threshold to de-escalate to Tier 2."""
    import time

    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=55)  # Enter tier 3

    # Still in tier 3 even though stress dropped
    action = manager.update(stress_total=30)  # Below 50 but above 15
    assert manager.current_tier == 3
    assert action is None

    # Simulate time passing
    manager._tier3_low_since = time.monotonic() - 6.0
    action = manager.update(stress_total=30)

    assert manager.current_tier == 2
    assert action == "tier3_exit"


def test_tier_manager_hysteresis_resets_on_spike():
    """Hysteresis timer resets if stress spikes back up."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Enter tier 2

    # Stress drops, start hysteresis
    manager.update(stress_total=10)
    assert manager._tier2_low_since is not None

    # Stress spikes back up - hysteresis should reset
    manager.update(stress_total=20)
    assert manager._tier2_low_since is None
    assert manager.current_tier == 2


def test_tier_manager_peak_tracking():
    """TierManager tracks peak stress during elevated state."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)
    manager.update(stress_total=35)
    manager.update(stress_total=25)

    assert manager.peak_stress == 35


def test_tier_manager_peak_resets_on_deescalation():
    """Peak stress resets when de-escalating to Tier 1."""
    import time

    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=30)  # Enter tier 2, peak=30
    assert manager.peak_stress == 30

    # Force de-escalation
    manager._tier2_low_since = time.monotonic() - 6.0
    manager.update(stress_total=5)  # Exit to tier 1

    assert manager.current_tier == 1
    assert manager.peak_stress == 0


def test_tier_manager_peak_returns_action_on_new_peak():
    """TierManager returns tier2_peak action when new peak is reached in Tier 2."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Entry

    action = manager.update(stress_total=30)  # New peak
    assert action == "tier2_peak"
    assert manager.peak_stress == 30

    action = manager.update(stress_total=25)  # Not a new peak
    assert action is None


def test_tier_manager_escalates_at_exact_threshold():
    """Stress exactly at threshold triggers escalation."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    # Exactly at tier 2 threshold
    action = manager.update(stress_total=15)
    assert manager.current_tier == 2
    assert action == "tier2_entry"

    # Exactly at tier 3 threshold
    action = manager.update(stress_total=50)
    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_entry_time_accessors():
    """TierManager exposes entry times for Daemon to read."""
    import time

    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    # Initially None
    assert manager.tier2_entry_time is None
    assert manager.tier3_entry_time is None

    # After entering tier 2
    manager.update(20)
    assert manager.tier2_entry_time is not None
    assert manager.tier3_entry_time is None

    # After entering tier 3
    manager.update(60)
    assert manager.tier3_entry_time is not None
    # tier2_entry_time may still be set (from earlier escalation)

    # After de-escalating from tier 3 to tier 2 (after hysteresis)
    manager._tier3_low_since = time.monotonic() - 10  # Force hysteresis
    manager.update(30)  # Below critical
    assert manager.tier3_entry_time is None  # Cleared on exit


# === Sentinel Tests ===


@pytest.fixture
def ring_buffer():
    """Create a fresh ring buffer for each test."""
    return RingBuffer(max_samples=100)


@pytest.fixture
def sentinel(ring_buffer):
    """Create a Sentinel instance with test-friendly intervals."""
    return Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,  # 50ms for faster tests
        elevated_threshold=15,
        critical_threshold=50,
    )


@pytest.mark.asyncio
async def test_sentinel_fast_loop_pushes_to_buffer(ring_buffer):
    """Fast loop pushes samples to the ring buffer at expected rate."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
    )

    # Run for ~250ms, expect at least 3 samples (with some margin for timing)
    async def run_briefly():
        task = asyncio.create_task(sentinel.start())
        await asyncio.sleep(0.25)
        sentinel.stop()
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.TimeoutError:
            pass

    with patch("pause_monitor.sentinel.collect_fast_metrics") as mock_metrics:
        mock_metrics.return_value = {
            "load_avg": 1.0,
            "memory_pressure": 80,
            "page_free_count": 100000,
        }
        await run_briefly()

    # Should have pushed at least 3 samples (250ms / 50ms = 5, minus startup/teardown)
    assert len(ring_buffer.samples) >= 3


@pytest.mark.asyncio
async def test_sentinel_triggers_snapshot_on_tier2_entry(ring_buffer):
    """Sentinel triggers process snapshot when entering Tier 2."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
        elevated_threshold=15,
        critical_threshold=50,
    )

    # Track tier changes
    tier_changes = []

    async def track_tier_change(action, tier):
        tier_changes.append((action, tier))

    sentinel.on_tier_change = track_tier_change

    async def run_with_stress():
        task = asyncio.create_task(sentinel.start())
        await asyncio.sleep(0.15)  # Let a few iterations run
        sentinel.stop()
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.TimeoutError:
            pass

    # Mock high stress to trigger tier 2
    with (
        patch("pause_monitor.sentinel.collect_fast_metrics") as mock_metrics,
        patch.object(ring_buffer, "snapshot_processes") as mock_snapshot,
    ):
        # High load triggers tier 2 via stress calculation
        mock_metrics.return_value = {
            "load_avg": 10.0,  # Very high load
            "memory_pressure": 5,  # Low memory pressure (high stress)
            "page_free_count": 100000,
        }
        await run_with_stress()

    # Should have triggered tier2_entry and snapshot
    assert any(action == "tier2_entry" for action, _ in tier_changes)
    mock_snapshot.assert_called()


@pytest.mark.asyncio
async def test_sentinel_triggers_snapshot_on_tier3_entry(ring_buffer):
    """Sentinel triggers process snapshot when entering Tier 3."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
        elevated_threshold=15,
        critical_threshold=50,
    )

    tier_changes = []

    async def track_tier_change(action, tier):
        tier_changes.append((action, tier))

    sentinel.on_tier_change = track_tier_change

    async def run_with_critical_stress():
        task = asyncio.create_task(sentinel.start())
        await asyncio.sleep(0.15)
        sentinel.stop()
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.TimeoutError:
            pass

    # Mock extremely high stress to trigger tier 3
    with (
        patch("pause_monitor.sentinel.collect_fast_metrics") as mock_metrics,
        patch.object(ring_buffer, "snapshot_processes") as mock_snapshot,
    ):
        mock_metrics.return_value = {
            "load_avg": 50.0,  # Extreme load
            "memory_pressure": 0,  # Critical memory pressure (0 = worst)
            "page_free_count": 1000,
        }
        await run_with_critical_stress()

    # Should have triggered tier3_entry
    assert any(action == "tier3_entry" for action, _ in tier_changes)
    mock_snapshot.assert_called()


@pytest.mark.asyncio
async def test_sentinel_detects_potential_pause(ring_buffer):
    """Sentinel detects potential pause when latency ratio > 2.0."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
    )

    pause_events = []

    async def track_pause(actual, expected, frozen_buffer):
        pause_events.append((actual, expected))

    sentinel.on_pause_detected = track_pause

    # Directly call the pause handler to test callback invocation
    await sentinel._handle_potential_pause(0.15, 0.05)  # 3x ratio

    assert len(pause_events) == 1
    actual, expected = pause_events[0]
    assert actual == 0.15
    assert expected == 0.05


@pytest.mark.asyncio
async def test_sentinel_clears_snapshots_on_tier2_exit(ring_buffer):
    """Sentinel clears snapshots when exiting Tier 2."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
        elevated_threshold=15,
        critical_threshold=50,
    )

    with patch.object(ring_buffer, "clear_snapshots") as mock_clear:
        # Simulate tier2_exit action handling
        await sentinel._handle_tier_action("tier2_exit")

    mock_clear.assert_called_once()


@pytest.mark.asyncio
async def test_sentinel_stop_halts_loops(ring_buffer):
    """Sentinel.stop() cleanly halts both loops."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=50,
    )

    with patch("pause_monitor.sentinel.collect_fast_metrics") as mock_metrics:
        mock_metrics.return_value = {
            "load_avg": 1.0,
            "memory_pressure": 80,
            "page_free_count": 100000,
        }

        task = asyncio.create_task(sentinel.start())
        await asyncio.sleep(0.1)

        sentinel.stop()
        # Should complete without hanging
        await asyncio.wait_for(task, timeout=1.0)

    # Verify it actually ran
    assert len(ring_buffer.samples) >= 1


def test_sentinel_initialization(ring_buffer):
    """Sentinel initializes with correct defaults."""
    sentinel = Sentinel(buffer=ring_buffer)

    assert sentinel.fast_interval == 0.1  # 100ms default
    assert sentinel.tier_manager.elevated_threshold == 15
    assert sentinel.tier_manager.critical_threshold == 50
    assert sentinel._running is False
    assert sentinel.on_tier_change is None
    assert sentinel.on_pause_detected is None


def test_sentinel_custom_thresholds(ring_buffer):
    """Sentinel accepts custom thresholds."""
    sentinel = Sentinel(
        buffer=ring_buffer,
        fast_interval_ms=200,
        elevated_threshold=20,
        critical_threshold=60,
    )

    assert sentinel.fast_interval == 0.2
    assert sentinel.tier_manager.elevated_threshold == 20
    assert sentinel.tier_manager.critical_threshold == 60
