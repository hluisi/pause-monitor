"""Tests for TierManager - tiered stress monitoring."""

import time

from pause_monitor.sentinel import TierManager


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
