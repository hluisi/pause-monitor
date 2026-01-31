# tests/test_tui.py
"""Tests for TUI app initialization."""

from rogue_hunter.config import Config


def test_tui_app_starts_without_crash(tmp_path):
    """TUI app initializes without errors."""
    from rogue_hunter.tui.app import RogueHunterApp

    # Just verify it can be instantiated
    app = RogueHunterApp(config=Config())
    assert app is not None
