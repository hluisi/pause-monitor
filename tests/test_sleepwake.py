"""Tests for sleep/wake detection."""

from datetime import datetime

from pause_monitor.sleepwake import (
    PauseDetector,
    PauseEvent,
    SleepWakeEvent,
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


# --- Pause Detection Tests ---


def test_pause_detector_no_pause_normal_latency():
    """No pause detected when latency is normal."""
    detector = PauseDetector(expected_interval=5.0)

    result = detector.check(actual_interval=5.2)
    assert result is None


def test_pause_detector_detects_pause():
    """Pause detected when actual interval >> expected."""
    detector = PauseDetector(expected_interval=5.0, pause_threshold=2.0)

    result = detector.check(actual_interval=15.0)

    assert result is not None
    assert isinstance(result, PauseEvent)
    assert result.duration == 15.0
    assert result.expected == 5.0


def test_pause_detector_ignores_sleep():
    """Pause not flagged if system was recently asleep."""
    detector = PauseDetector(expected_interval=5.0)

    # Simulate wake event
    wake_event = SleepWakeEvent(
        timestamp=datetime.now(),
        event_type=SleepWakeType.WAKE,
        reason="Lid Open",
    )

    result = detector.check(actual_interval=60.0, recent_wake=wake_event)

    assert result is None  # Not a pause, just woke up


def test_pause_detector_latency_ratio():
    """PauseEvent includes latency ratio."""
    detector = PauseDetector(expected_interval=5.0)

    result = detector.check(actual_interval=25.0)

    assert result is not None
    assert result.latency_ratio == 5.0
