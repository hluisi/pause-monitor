"""Tests for notification system."""

from pathlib import Path
from unittest.mock import patch

from pause_monitor.config import AlertsConfig
from pause_monitor.notifications import Notifier


def test_notifier_respects_enabled_flag():
    """Notifier does nothing when disabled."""
    config = AlertsConfig(enabled=False)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=5.0, event_dir=None)
        mock_send.assert_not_called()


def test_notifier_sends_pause_notification():
    """Notifier sends notification on pause detection."""
    config = AlertsConfig(enabled=True, pause_detected=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=5.0, event_dir=Path("/tmp/event"))

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "pause" in call_args.kwargs["title"].lower()


def test_notifier_respects_min_duration():
    """Notifier ignores pauses below minimum duration."""
    config = AlertsConfig(enabled=True, pause_detected=True, pause_min_duration=3.0)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=2.0, event_dir=None)
        mock_send.assert_not_called()

        notifier.pause_detected(duration=3.5, event_dir=None)
        mock_send.assert_called_once()


def test_notifier_critical_stress():
    """Notifier sends critical stress notification."""
    config = AlertsConfig(enabled=True, critical_stress=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.critical_stress(stress_total=75, duration=60)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "critical" in call_args.kwargs["title"].lower()


def test_notifier_forensics_completed():
    """Notifier sends forensics completion notification."""
    config = AlertsConfig(enabled=True, forensics_completed=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.forensics_completed(event_dir=Path("/tmp/event"))

        mock_send.assert_called_once()
