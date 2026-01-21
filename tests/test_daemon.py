"""Tests for daemon core."""

from datetime import datetime

from pause_monitor.config import Config
from pause_monitor.daemon import Daemon, DaemonState


def test_daemon_state_initial():
    """DaemonState initializes with correct defaults."""
    state = DaemonState()

    assert state.running is False
    assert state.sample_count == 0
    assert state.last_sample_time is None
    assert state.current_stress == 0


def test_daemon_state_update_sample():
    """DaemonState updates on new sample."""
    state = DaemonState()

    state.update_sample(stress=25, timestamp=datetime.now())

    assert state.sample_count == 1
    assert state.current_stress == 25
    assert state.last_sample_time is not None


def test_daemon_state_elevated_duration():
    """DaemonState tracks elevated duration."""
    state = DaemonState()

    state.enter_elevated()
    assert state.elevated_since is not None

    duration = state.elevated_duration
    assert duration >= 0


def test_daemon_init_creates_components():
    """Daemon initializes all required components."""
    config = Config()
    daemon = Daemon(config)

    assert daemon.config is config
    assert daemon.state is not None
    assert daemon.policy is not None
    assert daemon.notifier is not None
    assert daemon.io_baseline is not None
    assert daemon.pause_detector is not None
    assert daemon.core_count > 0
