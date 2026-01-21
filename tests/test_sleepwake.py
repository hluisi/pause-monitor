"""Tests for sleep/wake detection."""

from pause_monitor.sleepwake import (
    SleepWakeType,
    parse_pmset_log,
)

# Real pmset log format - keep as realistic test data
# ruff: noqa: E501
SAMPLE_PMSET_OUTPUT = """
2024-01-15 10:30:15 -0500 Sleep                   Entering Sleep state due to 'Software Sleep pid=1234':
2024-01-15 10:30:20 -0500 Kernel Idle sleep preventers: IODisplayWrangler
2024-01-15 10:35:45 -0500 Wake                    Wake from Normal Sleep [CDNVA] : due to EC.LidOpen/Lid Open
2024-01-15 14:20:00 -0500 Sleep                   Entering Sleep state due to 'Idle Sleep':
2024-01-15 14:45:30 -0500 DarkWake                DarkWake from Normal Sleep [CDN] : due to EC.PowerButton/
"""


def test_parse_pmset_finds_sleep_events():
    """Parser extracts sleep events from pmset output."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    sleep_events = [e for e in events if e.event_type == SleepWakeType.SLEEP]
    assert len(sleep_events) == 2


def test_parse_pmset_finds_wake_events():
    """Parser extracts wake events from pmset output."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    wake_events = [e for e in events if e.event_type == SleepWakeType.WAKE]
    assert len(wake_events) == 1


def test_parse_pmset_finds_darkwake_events():
    """Parser extracts DarkWake events."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    darkwake_events = [e for e in events if e.event_type == SleepWakeType.DARK_WAKE]
    assert len(darkwake_events) == 1


def test_parse_pmset_extracts_timestamp():
    """Parser extracts correct timestamps."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    assert first_sleep.timestamp.year == 2024
    assert first_sleep.timestamp.month == 1
    assert first_sleep.timestamp.day == 15


def test_parse_pmset_extracts_reason():
    """Parser extracts sleep/wake reason."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    assert "Software Sleep" in first_sleep.reason


def test_sleep_wake_event_duration():
    """SleepWakeEvent calculates duration to next event."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    first_wake = events[1]

    duration = (first_wake.timestamp - first_sleep.timestamp).total_seconds()
    assert 300 <= duration <= 400  # About 5 minutes of sleep
