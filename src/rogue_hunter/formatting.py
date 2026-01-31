"""Formatting utilities for consistent output across CLI and TUI."""

import time


def format_duration(
    entry_time: float,
    exit_time: float | None,
    *,
    now: float | None = None,
) -> str:
    """Format event duration for list/table views (compact).

    Args:
        entry_time: Event start timestamp
        exit_time: Event end timestamp, or None if ongoing
        now: Current time for ongoing duration calculation (defaults to time.time())

    Returns:
        Formatted duration string:
        - Closed events: "1.5s"
        - Ongoing events: "10s*" (asterisk indicates ongoing)
    """
    if exit_time is not None:
        duration = exit_time - entry_time
        return f"{duration:.1f}s"
    else:
        if now is None:
            now = time.time()
        duration = now - entry_time
        return f"{duration:.0f}s*"


def format_duration_verbose(
    entry_time: float,
    exit_time: float | None,
) -> str:
    """Format event duration for detail views (verbose).

    Args:
        entry_time: Event start timestamp
        exit_time: Event end timestamp, or None if ongoing

    Returns:
        Formatted duration string:
        - Closed events: "1.5s"
        - Ongoing events: "ongoing"
    """
    if exit_time is not None:
        duration = exit_time - entry_time
        return f"{duration:.1f}s"
    else:
        return "ongoing"


def calculate_duration(
    entry_time: float,
    exit_time: float | None,
) -> float | None:
    """Calculate raw duration in seconds for data export.

    Args:
        entry_time: Event start timestamp
        exit_time: Event end timestamp, or None if ongoing

    Returns:
        Duration in seconds, or None if event is ongoing
    """
    if exit_time is not None:
        return exit_time - entry_time
    return None
