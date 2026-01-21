"""Sleep/Wake detection for pause-monitor."""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import structlog

log = structlog.get_logger()


class SleepWakeType(Enum):
    """Type of sleep/wake event."""

    SLEEP = "sleep"
    WAKE = "wake"
    DARK_WAKE = "dark_wake"


@dataclass
class SleepWakeEvent:
    """A single sleep or wake event."""

    timestamp: datetime
    event_type: SleepWakeType
    reason: str


@dataclass
class PauseEvent:
    """A detected system pause (not sleep-related)."""

    timestamp: datetime
    duration: float
    expected: float
    latency_ratio: float


class PauseDetector:
    """Detect system pauses via timing anomalies."""

    def __init__(self, expected_interval: float, pause_threshold: float = 2.0):
        """Initialize pause detector.

        Args:
            expected_interval: Expected seconds between samples
            pause_threshold: Ratio above which is considered a pause
        """
        self.expected_interval = expected_interval
        self.pause_threshold = pause_threshold

    def check(
        self,
        actual_interval: float,
        recent_wake: SleepWakeEvent | None = None,
    ) -> PauseEvent | None:
        """Check if the interval indicates a pause.

        Args:
            actual_interval: Actual time elapsed since last sample
            recent_wake: If system recently woke from sleep

        Returns:
            PauseEvent if pause detected, None otherwise
        """
        latency_ratio = actual_interval / self.expected_interval

        # Not a pause if ratio is below threshold
        if latency_ratio < self.pause_threshold:
            return None

        # Not a pause if we just woke from sleep
        if recent_wake is not None:
            log.debug(
                "pause_suppressed_by_wake",
                actual=actual_interval,
                expected=self.expected_interval,
                wake_reason=recent_wake.reason,
            )
            return None

        return PauseEvent(
            timestamp=datetime.now(),
            duration=actual_interval,
            expected=self.expected_interval,
            latency_ratio=latency_ratio,
        )


# Pattern to match pmset log entries
PMSET_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+[+-]\d{4}\s+"
    r"(Sleep|Wake|DarkWake)\s+(.+)"
)


def parse_pmset_log(output: str) -> list[SleepWakeEvent]:
    """Parse pmset -g log output for sleep/wake events.

    Args:
        output: Raw output from `pmset -g log`

    Returns:
        List of SleepWakeEvent in chronological order
    """
    events = []

    for line in output.splitlines():
        match = PMSET_PATTERN.search(line)
        if not match:
            continue

        timestamp_str, event_type_str, reason = match.groups()

        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        if event_type_str == "Sleep":
            event_type = SleepWakeType.SLEEP
        elif event_type_str == "Wake":
            event_type = SleepWakeType.WAKE
        elif event_type_str == "DarkWake":
            event_type = SleepWakeType.DARK_WAKE
        else:
            continue

        events.append(
            SleepWakeEvent(
                timestamp=timestamp,
                event_type=event_type,
                reason=reason.strip(),
            )
        )

    return events


def get_recent_sleep_events(since: datetime | None = None) -> list[SleepWakeEvent]:
    """Get recent sleep/wake events from system logs.

    Args:
        since: Only return events after this time. Defaults to 1 hour ago.

    Returns:
        List of SleepWakeEvent in chronological order
    """
    if since is None:
        since = datetime.now() - timedelta(hours=1)

    try:
        result = subprocess.run(
            ["pmset", "-g", "log"],
            capture_output=True,
            timeout=5,
        )
        # Decode with error handling - pmset log can contain non-UTF-8 bytes
        stdout = result.stdout.decode("utf-8", errors="replace")
        events = parse_pmset_log(stdout)
        return [e for e in events if e.timestamp >= since]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("pmset_log_failed", error=str(e))
        return []


def was_recently_asleep(within_seconds: float = 10.0) -> SleepWakeEvent | None:
    """Check if system recently woke from sleep.

    Args:
        within_seconds: How recent counts as "recent"

    Returns:
        The wake event if found, None otherwise
    """
    now = datetime.now()
    events = get_recent_sleep_events(since=now - timedelta(seconds=within_seconds * 2))

    for event in reversed(events):
        if event.event_type in (SleepWakeType.WAKE, SleepWakeType.DARK_WAKE):
            if (now - event.timestamp).total_seconds() <= within_seconds:
                return event

    return None
