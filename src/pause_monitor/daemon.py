"""Background daemon for pause-monitor."""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.collector import PowermetricsStream, SamplePolicy, get_core_count
from pause_monitor.config import Config
from pause_monitor.notifications import Notifier
from pause_monitor.sleepwake import PauseDetector
from pause_monitor.stress import IOBaselineManager

log = structlog.get_logger()


@dataclass
class DaemonState:
    """Runtime state of the daemon."""

    running: bool = False
    sample_count: int = 0
    event_count: int = 0
    last_sample_time: datetime | None = None
    current_stress: int = 0
    elevated_since: datetime | None = None
    critical_since: datetime | None = None

    def update_sample(self, stress: int, timestamp: datetime) -> None:
        """Update state after a sample."""
        self.sample_count += 1
        self.current_stress = stress
        self.last_sample_time = timestamp

    def enter_elevated(self) -> None:
        """Mark entering elevated state."""
        if self.elevated_since is None:
            self.elevated_since = datetime.now()

    def exit_elevated(self) -> None:
        """Mark exiting elevated state."""
        self.elevated_since = None

    def enter_critical(self) -> None:
        """Mark entering critical state."""
        if self.critical_since is None:
            self.critical_since = datetime.now()

    def exit_critical(self) -> None:
        """Mark exiting critical state."""
        self.critical_since = None

    @property
    def elevated_duration(self) -> float:
        """Seconds in elevated state."""
        if self.elevated_since is None:
            return 0.0
        return (datetime.now() - self.elevated_since).total_seconds()

    @property
    def critical_duration(self) -> float:
        """Seconds in critical state."""
        if self.critical_since is None:
            return 0.0
        return (datetime.now() - self.critical_since).total_seconds()


class Daemon:
    """Main daemon class orchestrating sampling and detection."""

    def __init__(self, config: Config):
        self.config = config
        self.state = DaemonState()

        # Initialize components
        self.policy = SamplePolicy(
            normal_interval=config.sampling.normal_interval,
            elevated_interval=config.sampling.elevated_interval,
            elevation_threshold=config.sampling.elevation_threshold,
            critical_threshold=config.sampling.critical_threshold,
        )

        self.notifier = Notifier(config.alerts)
        self.io_baseline = IOBaselineManager(persisted_baseline=None)
        self.pause_detector = PauseDetector(
            expected_interval=config.sampling.normal_interval,
        )
        self.core_count = get_core_count()

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
        self._powermetrics: PowermetricsStream | None = None
        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
