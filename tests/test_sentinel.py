"""Tests for sentinel module - fast stress monitoring."""

from pause_monitor.sentinel import collect_fast_metrics


def test_collect_fast_metrics_returns_dict():
    """Fast metrics collection returns required fields."""
    metrics = collect_fast_metrics()

    assert "load_avg" in metrics
    assert "memory_pressure" in metrics
    assert "page_free_count" in metrics
    assert isinstance(metrics["load_avg"], float)
