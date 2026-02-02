# tests/test_tui.py
"""Tests for TUI app initialization."""

from pathlib import Path

from rogue_hunter.config import Config


def test_tui_app_starts_without_crash(tmp_path):
    """TUI app initializes without errors."""
    from rogue_hunter.tui.app import RogueHunterApp

    # Just verify it can be instantiated
    app = RogueHunterApp(config=Config())
    assert app is not None


def test_format_share():
    """format_share displays resource share values with 2 decimal precision."""
    from rogue_hunter.tui.app import format_share

    # All values use consistent 2 decimal precision
    assert format_share(150.0) == "150.00x"
    assert format_share(100.0) == "100.00x"
    assert format_share(50.5) == "50.50x"
    assert format_share(10.0) == "10.00x"
    assert format_share(5.5) == "5.50x"
    assert format_share(1.0) == "1.00x"
    assert format_share(0.5) == "0.50x"
    assert format_share(0.05) == "0.05x"


def test_format_dominant_info():
    """format_dominant_info displays dominant_resource with disproportionality, right-justified."""
    from rogue_hunter.tui.app import format_dominant_info

    # All values use consistent 2 decimal precision, right-justified
    # Format: "LABEL" (4 chars right) + " " + value (7 chars) + "x"
    assert format_dominant_info("cpu", 150.0) == " CPU  150.00x"
    assert format_dominant_info("gpu", 50.5) == " GPU   50.50x"
    assert format_dominant_info("memory", 5.5) == " MEM    5.50x"
    assert format_dominant_info("disk", 0.5) == "DISK    0.50x"
    assert format_dominant_info("wakeups", 10.0) == "WAKE   10.00x"

    # Test resource labels
    assert "CPU" in format_dominant_info("cpu", 1.0)
    assert "GPU" in format_dominant_info("gpu", 1.0)
    assert "MEM" in format_dominant_info("memory", 1.0)
    assert "DISK" in format_dominant_info("disk", 1.0)
    assert "WAKE" in format_dominant_info("wakeups", 1.0)


def test_tui_no_category_references():
    """TUI code does not reference old category fields."""
    # Old fields that should not appear in TUI code
    old_fields = [
        "dominant_category",
        "dominant_metrics",
        "blocking_score",
        "contention_score",
        "pressure_score",
        "efficiency_score",
    ]

    # Scan all *.py files in src/rogue_hunter/tui/
    tui_dir = Path(__file__).parent.parent / "src" / "rogue_hunter" / "tui"
    for py_file in tui_dir.glob("*.py"):
        content = py_file.read_text()
        for old_field in old_fields:
            assert old_field not in content, f"Found '{old_field}' in {py_file.name}"
