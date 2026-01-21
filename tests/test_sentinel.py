"""Tests for sentinel module - fast stress monitoring."""

from pause_monitor.sentinel import collect_fast_metrics


def test_collect_fast_metrics_returns_dict():
    """Fast metrics collection returns required fields."""
    metrics = collect_fast_metrics()

    assert "load_avg" in metrics
    assert "memory_pressure" in metrics
    assert "page_free_count" in metrics
    assert isinstance(metrics["load_avg"], float)


def test_sysctl_int_returns_none_for_invalid():
    """Invalid sysctl names return None."""
    from pause_monitor.sysctl import sysctl_int

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
