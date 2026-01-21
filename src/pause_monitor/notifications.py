"""macOS notification system for pause-monitor."""

import subprocess
from enum import Enum
from pathlib import Path

import structlog

from pause_monitor.config import AlertsConfig

log = structlog.get_logger()


class NotificationType(Enum):
    """Types of notifications."""

    PAUSE_DETECTED = "pause_detected"
    CRITICAL_STRESS = "critical_stress"
    ELEVATED_ENTERED = "elevated_entered"
    FORENSICS_COMPLETED = "forensics_completed"


def send_notification(
    title: str,
    message: str,
    sound: bool = True,
    subtitle: str | None = None,
) -> bool:
    """Send a macOS notification via osascript.

    Args:
        title: Notification title
        message: Notification body
        sound: Whether to play default sound
        subtitle: Optional subtitle

    Returns:
        True if notification was sent successfully
    """
    sound_part = 'sound name "Funk"' if sound else ""
    subtitle_part = f'subtitle "{subtitle}"' if subtitle else ""

    script = f'''
    display notification "{message}" with title "{title}" {subtitle_part} {sound_part}
    '''

    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
        log.debug("notification_sent", title=title)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("notification_failed", error=str(e))
        return False


class Notifier:
    """Manages notifications based on alert configuration."""

    def __init__(self, config: AlertsConfig):
        self.config = config
        self._critical_start_time: float | None = None

    def pause_detected(self, duration: float, event_dir: Path | None) -> None:
        """Notify about detected pause."""
        if not self.config.enabled or not self.config.pause_detected:
            return

        if duration < self.config.pause_min_duration:
            return

        message = f"System was unresponsive for {duration:.1f}s"
        if event_dir:
            message += f"\nForensics: {event_dir.name}"

        send_notification(
            title="Pause Detected",
            message=message,
            sound=self.config.sound,
        )

    def critical_stress(self, stress_total: int, duration: float) -> None:
        """Notify about sustained critical stress."""
        if not self.config.enabled or not self.config.critical_stress:
            return

        if duration < self.config.critical_duration:
            return

        send_notification(
            title="Critical System Stress",
            message=f"Stress score {stress_total} for {duration:.0f}s",
            sound=self.config.sound,
        )

    def elevated_entered(self, stress_total: int) -> None:
        """Notify about entering elevated monitoring."""
        if not self.config.enabled or not self.config.elevated_entered:
            return

        send_notification(
            title="Elevated Monitoring",
            message=f"Stress score {stress_total} - sampling increased",
            sound=self.config.sound,
        )

    def forensics_completed(self, event_dir: Path) -> None:
        """Notify that forensics capture completed."""
        if not self.config.enabled or not self.config.forensics_completed:
            return

        send_notification(
            title="Forensics Capture Complete",
            message=f"Saved to {event_dir.name}",
            sound=self.config.sound,
        )
