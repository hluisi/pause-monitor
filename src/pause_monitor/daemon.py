"""Background daemon for pause-monitor."""

import asyncio
import os
import signal
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.collector import get_core_count
from pause_monitor.config import Config
from pause_monitor.forensics import (
    ForensicsCapture,
    create_event_dir,
    identify_culprits,
    run_full_capture,
)
from pause_monitor.notifications import Notifier
from pause_monitor.ringbuffer import BufferContents, RingBuffer
from pause_monitor.sentinel import Sentinel, TierAction, TierManager
from pause_monitor.sleepwake import was_recently_asleep
from pause_monitor.storage import (
    Event,
    init_database,
    insert_event,
    migrate_add_event_status,
    migrate_add_stress_columns,
    prune_old_data,
)
from pause_monitor.stress import (
    IOBaselineManager,
    StressBreakdown,
)

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

        self.notifier = Notifier(config.alerts)
        self.io_baseline = IOBaselineManager(persisted_baseline=None)
        self.core_count = get_core_count()

        # Initialize ring buffer: ring_buffer_seconds * 10 samples (10Hz fast loop)
        max_samples = config.sentinel.ring_buffer_seconds * 10
        self.ring_buffer = RingBuffer(max_samples=max_samples)

        # Initialize sentinel with config values
        self.sentinel = Sentinel(
            buffer=self.ring_buffer,
            fast_interval_ms=config.sentinel.fast_interval_ms,
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )

        # Wire up sentinel callbacks
        self.sentinel.on_tier_change = self._handle_tier_change
        self.sentinel.on_pause_detected = self._handle_pause_from_sentinel

        # Tier management (replaces sentinel.tier_manager)
        self.tier_manager = TierManager(
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
        self._auto_prune_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the daemon."""
        log.info("daemon_starting")

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        # Check for existing instance
        if self._check_already_running():
            log.error("daemon_already_running")
            raise RuntimeError("Daemon is already running")

        self._write_pid_file()

        # Initialize database
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        init_database(self.config.db_path)
        self._conn = sqlite3.connect(self.config.db_path)

        # Run migrations for existing databases
        migrate_add_event_status(self._conn)
        migrate_add_stress_columns(self._conn)

        # Start caffeinate to prevent App Nap
        await self._start_caffeinate()

        self.state.running = True
        log.info("daemon_started")

        # Start auto-prune task (tracked for cleanup)
        self._auto_prune_task = asyncio.create_task(self._auto_prune())

        # Run sentinel (replaces the old powermetrics-based _run_loop)
        await self.sentinel.start()

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        log.info("daemon_stopping")
        self.state.running = False

        # Stop sentinel
        self.sentinel.stop()

        # Cancel auto-prune task
        if self._auto_prune_task:
            self._auto_prune_task.cancel()
            try:
                await self._auto_prune_task
            except asyncio.CancelledError:
                pass
            self._auto_prune_task = None

        # Stop caffeinate
        await self._stop_caffeinate()

        # Close database
        if self._conn:
            self._conn.close()
            self._conn = None

        self._remove_pid_file()

        log.info("daemon_stopped")

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signals."""
        log.info("signal_received", signal=sig.name)
        self._shutdown_event.set()
        # Stop sentinel synchronously (it will cause sentinel.start() to return)
        self.sentinel.stop()

    async def _start_caffeinate(self) -> None:
        """Start caffeinate to prevent App Nap."""
        try:
            self._caffeinate_proc = await asyncio.create_subprocess_exec(
                "/usr/bin/caffeinate",
                "-i",  # Prevent idle sleep
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            log.debug("caffeinate_started")
        except FileNotFoundError:
            log.warning("caffeinate_not_found")

    async def _stop_caffeinate(self) -> None:
        """Stop caffeinate subprocess."""
        if self._caffeinate_proc:
            self._caffeinate_proc.terminate()
            try:
                await asyncio.wait_for(self._caffeinate_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._caffeinate_proc.kill()
            self._caffeinate_proc = None
            log.debug("caffeinate_stopped")

    def _write_pid_file(self) -> None:
        """Write PID file."""
        self.config.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.pid_path.write_text(str(os.getpid()))
        log.debug("pid_file_written", path=str(self.config.pid_path))

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        if self.config.pid_path.exists():
            self.config.pid_path.unlink()
            log.debug("pid_file_removed")

    def _check_already_running(self) -> bool:
        """Check if daemon is already running."""
        if not self.config.pid_path.exists():
            return False

        try:
            pid = int(self.config.pid_path.read_text().strip())
            # Check if process exists
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file exists but process doesn't - stale file
            self._remove_pid_file()
            return False

    async def _auto_prune(self) -> None:
        """Run automatic data pruning daily."""
        while not self._shutdown_event.is_set():
            try:
                # Wait for 24 hours or shutdown
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=86400,  # 24 hours
                )
                break
            except asyncio.TimeoutError:
                # Run prune
                if self._conn:
                    log.info("auto_prune_starting")
                    deleted = prune_old_data(
                        self._conn,
                        samples_days=self.config.retention.samples_days,
                        events_days=self.config.retention.events_days,
                    )
                    log.info("auto_prune_completed", samples=deleted[0], events=deleted[1])

    async def _run_forensics(self, capture: ForensicsCapture) -> None:
        """Run forensics capture and notify on completion."""
        try:
            await run_full_capture(capture)
            self.notifier.forensics_completed(capture.event_dir)
        except Exception as e:
            log.exception(
                "forensics_capture_failed",
                event_dir=str(capture.event_dir),
                error=str(e),
            )

    # === Sentinel Callbacks ===

    async def _handle_tier_change(self, action: TierAction, tier: int) -> None:
        """Handle tier state changes from sentinel.

        Args:
            action: The tier action (TIER2_ENTRY, TIER2_EXIT, TIER3_ENTRY, TIER3_EXIT, TIER2_PEAK)
            tier: The current tier (1, 2, or 3)
        """
        log.info("tier_change", action=action, tier=tier)

        # Update state based on tier
        if action == TierAction.TIER2_ENTRY or action == TierAction.TIER3_ENTRY:
            self.state.enter_elevated()
            if action == TierAction.TIER3_ENTRY:
                self.state.enter_critical()
        elif action == TierAction.TIER2_EXIT:
            self.state.exit_elevated()
        elif action == TierAction.TIER3_EXIT:
            self.state.exit_critical()

        # Send notifications for tier changes
        if action == TierAction.TIER2_ENTRY:
            self.notifier.elevated_entered(self.sentinel.tier_manager.peak_stress)
        elif action == TierAction.TIER3_ENTRY:
            # Critical stress notification
            self.notifier.critical_stress(
                self.sentinel.tier_manager.peak_stress,
                0.0,  # Just entered, no duration yet
            )

    async def _handle_pause_from_sentinel(
        self,
        actual: float,
        expected: float,
        contents: BufferContents,
    ) -> None:
        """Handle pause detection from sentinel.

        Args:
            actual: Actual elapsed time
            expected: Expected interval
            contents: Frozen ring buffer contents
        """
        # Check for recent sleep/wake
        recent_wake = was_recently_asleep(within_seconds=actual)
        if recent_wake is not None:
            log.info(
                "pause_excluded_sleep",
                actual=actual,
                expected=expected,
                wake_reason=recent_wake.reason,
            )
            return

        # Check minimum duration threshold
        if actual < self.config.alerts.pause_min_duration:
            log.debug(
                "pause_below_threshold",
                duration=actual,
                min_duration=self.config.alerts.pause_min_duration,
            )
            return

        duration = actual
        timestamp = datetime.now()

        log.warning(
            "pause_detected",
            duration=duration,
            latency_ratio=actual / expected,
        )

        # Create forensics capture
        event_dir = create_event_dir(self.config.events_dir, timestamp)
        capture = ForensicsCapture(event_dir)

        # Write ring buffer contents
        capture.write_ring_buffer(contents)

        # Identify culprits from buffer contents
        culprits = identify_culprits(contents)

        # Write metadata
        capture.write_metadata(
            {
                "timestamp": timestamp.isoformat(),
                "duration": duration,
                "expected_interval": expected,
                "latency_ratio": actual / expected,
                "tier": self.sentinel.tier_manager.current_tier,
                "peak_stress": self.sentinel.tier_manager.peak_stress,
                "culprits": culprits,
            }
        )

        # Run forensics capture in background
        asyncio.create_task(self._run_forensics(capture))

        # Compute average stress from buffer for the event record
        if contents.samples:
            avg_stress = contents.samples[-1].stress  # Use most recent stress
        else:
            avg_stress = StressBreakdown(
                load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0
            )

        # Create event record
        event = Event(
            timestamp=timestamp,
            duration=duration,
            stress=avg_stress,
            culprits=culprits,
            event_dir=str(event_dir),
            notes=None,
        )

        if self._conn:
            insert_event(self._conn, event)

        self.state.event_count += 1

        # Send notification
        self.notifier.pause_detected(duration, event_dir)


async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon until shutdown.

    Args:
        config: Optional config, loads from file if not provided
    """
    if config is None:
        config = Config.load()

    # Setup logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )

    daemon = Daemon(config)

    try:
        await daemon.start()
    except Exception as e:
        log.exception("daemon_crashed", error=str(e))
        raise
    finally:
        await daemon.stop()
