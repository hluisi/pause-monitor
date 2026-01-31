"""Tests for formatting utilities."""

import time

import pytest

from rogue_hunter.formatting import (
    calculate_duration,
    format_duration,
    format_duration_verbose,
)


class TestFormatDuration:
    """Tests for format_duration (compact list view format)."""

    def test_closed_event(self) -> None:
        """Closed events show duration with one decimal."""
        result = format_duration(1000.0, 1001.5)
        assert result == "1.5s"

    def test_closed_event_longer(self) -> None:
        """Longer durations format correctly."""
        result = format_duration(1000.0, 1125.7)
        assert result == "125.7s"

    def test_ongoing_event_with_now(self) -> None:
        """Ongoing events show asterisk marker."""
        result = format_duration(1000.0, None, now=1010.0)
        assert result == "10s*"

    def test_ongoing_event_without_now(self) -> None:
        """Ongoing events use current time if now not provided."""
        entry = time.time() - 5  # 5 seconds ago
        result = format_duration(entry, None)
        # Should be approximately 5s* (allow for test execution time)
        assert result.endswith("s*")
        duration = int(result[:-2])  # Strip "s*"
        assert 4 <= duration <= 6


class TestFormatDurationVerbose:
    """Tests for format_duration_verbose (detail view format)."""

    def test_closed_event(self) -> None:
        """Closed events show duration with one decimal."""
        result = format_duration_verbose(1000.0, 1001.5)
        assert result == "1.5s"

    def test_ongoing_event(self) -> None:
        """Ongoing events show 'ongoing' text."""
        result = format_duration_verbose(1000.0, None)
        assert result == "ongoing"


class TestCalculateDuration:
    """Tests for calculate_duration (raw value for export)."""

    def test_closed_event(self) -> None:
        """Closed events return duration in seconds."""
        result = calculate_duration(1000.0, 1125.7)
        assert result == pytest.approx(125.7)

    def test_ongoing_event(self) -> None:
        """Ongoing events return None."""
        result = calculate_duration(1000.0, None)
        assert result is None
