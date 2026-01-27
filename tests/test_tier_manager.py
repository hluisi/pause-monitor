"""Tests for TierManager - tiered score monitoring."""

from pause_monitor.sentinel import TierManager


def test_tier_manager_starts_at_tier1():
    """TierManager starts in Tier 1 (Sentinel)."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    assert manager.current_tier == 1


def test_tier_manager_escalates_to_tier2():
    """Score >= elevated_threshold triggers escalation to Tier 2."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    action = manager.update(score=20)

    assert manager.current_tier == 2
    assert action == "tier2_entry"


def test_tier_manager_escalates_to_tier3():
    """Score >= critical_threshold triggers escalation to Tier 3."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=20)  # Enter tier 2 first

    action = manager.update(score=55)

    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_direct_escalation_to_tier3():
    """Score >= critical_threshold triggers direct escalation from Tier 1 to Tier 3."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    action = manager.update(score=55)

    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_deescalates_immediately():
    """Tier 2 de-escalates immediately when score drops below threshold."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=20)  # Enter tier 2

    action = manager.update(score=10)

    assert manager.current_tier == 1
    assert action == "tier2_exit"


def test_tier_manager_tier3_deescalates_immediately():
    """Tier 3 de-escalates immediately to Tier 2 when score drops below critical."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=55)  # Enter tier 3

    action = manager.update(score=30)  # Below 50 but above 15

    assert manager.current_tier == 2
    assert action == "tier3_exit"


def test_tier_manager_peak_tracking():
    """TierManager tracks peak score during elevated state."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=20)
    manager.update(score=35)
    manager.update(score=25)

    assert manager.peak_score == 35


def test_tier_manager_peak_resets_on_deescalation():
    """Peak score resets when de-escalating to Tier 1."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=30)  # Enter tier 2, peak=30
    assert manager.peak_score == 30

    # De-escalation is now immediate
    manager.update(score=5)  # Exit to tier 1

    assert manager.current_tier == 1
    assert manager.peak_score == 0


def test_tier_manager_peak_returns_action_on_new_peak():
    """TierManager returns tier2_peak action when new peak is reached in Tier 2."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=20)  # Entry

    action = manager.update(score=30)  # New peak
    assert action == "tier2_peak"
    assert manager.peak_score == 30

    action = manager.update(score=25)  # Not a new peak
    assert action is None


def test_tier_manager_peak_not_returned_when_score_equals_existing_peak():
    """TierManager should NOT return tier2_peak when score equals (but doesn't exceed) peak.

    This prevents duplicate sample saving when score stays at peak level.
    """
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(score=20)  # Entry

    action = manager.update(score=30)  # New peak
    assert action == "tier2_peak"

    # Same score again - should NOT be a new peak
    action = manager.update(score=30)
    assert action is None  # Not tier2_peak!

    # And again
    action = manager.update(score=30)
    assert action is None


def test_tier_manager_escalates_at_exact_threshold():
    """Score exactly at threshold triggers escalation."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    # Exactly at tier 2 threshold
    action = manager.update(score=15)
    assert manager.current_tier == 2
    assert action == "tier2_entry"

    # Exactly at tier 3 threshold
    action = manager.update(score=50)
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

    # After de-escalating from tier 3 to tier 2 (immediate)
    manager.update(30)  # Below critical
    assert manager.tier3_entry_time is None  # Cleared on exit


def test_tier_manager_uses_config_thresholds():
    """TierManager should use thresholds from BandsConfig."""
    from pause_monitor.config import BandsConfig

    bands = BandsConfig()
    tm = TierManager(
        elevated_threshold=bands.tracking_threshold,
        critical_threshold=bands.forensics_threshold,
    )
    assert tm._elevated_threshold == bands.tracking_threshold
    assert tm._critical_threshold == bands.forensics_threshold


def test_tier_manager_peak_score_property():
    """Should have peak_score property."""
    tm = TierManager(elevated_threshold=30, critical_threshold=60)
    tm.update(40)  # Enter tier 2
    tm.update(50)  # Higher score

    assert tm.peak_score == 50
