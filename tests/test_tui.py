# tests/test_tui.py
"""Tests for TUI app initialization."""

from pause_monitor.config import Config


def test_tui_app_starts_without_crash(tmp_path):
    """TUI app initializes without errors."""
    from pause_monitor.tui.app import PauseMonitorApp

    # Just verify it can be instantiated
    app = PauseMonitorApp(config=Config())
    assert app is not None
