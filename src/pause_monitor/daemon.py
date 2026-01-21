"""Background daemon for pause-monitor."""

import asyncio
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.collector import (
    PolicyResult,
    PowermetricsResult,
    PowermetricsStream,
    SamplePolicy,
    SamplingState,
    get_core_count,
    get_system_metrics,
)
from pause_monitor.config import Config
from pause_monitor.forensics import ForensicsCapture, create_event_dir, run_full_capture
from pause_monitor.notifications import Notifier
from pause_monitor.sleepwake import PauseDetector, PauseEvent, was_recently_asleep
from pause_monitor.storage import Event, Sample, init_database, insert_event, insert_sample
from pause_monitor.stress import (
    IOBaselineManager,
    StressBreakdown,
    calculate_stress,
    get_memory_pressure_fast,
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

        # Start caffeinate to prevent App Nap
        await self._start_caffeinate()

        # Initialize powermetrics stream (started in _run_loop)
        self._powermetrics = PowermetricsStream(interval_ms=self.policy.current_interval * 1000)

        self.state.running = True
        log.info("daemon_started")

        # Main loop
        await self._run_loop()

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        log.info("daemon_stopping")
        self.state.running = False

        # Stop powermetrics
        if self._powermetrics:
            await self._powermetrics.stop()
            self._powermetrics = None

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

    async def _run_loop(self) -> None:
        """Main sampling loop - streams powermetrics and processes samples."""
        # Start powermetrics stream
        await self._powermetrics.start()

        last_sample_time = time.monotonic()

        try:
            async for pm_result in self._powermetrics.read_samples():
                if self._shutdown_event.is_set():
                    break

                # Calculate actual interval (for pause detection)
                now = time.monotonic()
                actual_interval = now - last_sample_time
                last_sample_time = now

                # Check for pause first
                await self._check_for_pause(actual_interval)

                # Collect and store sample
                await self._collect_sample(pm_result, actual_interval)

                # Update pause detector's expected interval based on current policy
                self.pause_detector.expected_interval = self.policy.current_interval

        except asyncio.CancelledError:
            log.info("sampling_loop_cancelled")
        except Exception as e:
            log.exception("sampling_loop_error", error=str(e))
            raise

    async def _collect_sample(
        self,
        pm_result: PowermetricsResult,
        interval: float,
    ) -> Sample:
        """Collect a complete sample from all sources."""
        now = datetime.now()

        # Get system metrics
        sys_metrics = get_system_metrics()

        # Get memory pressure
        mem_pct = get_memory_pressure_fast()

        # Calculate I/O rate
        io_rate = sys_metrics.io_read + sys_metrics.io_write
        self.io_baseline.update(io_rate)

        # Calculate latency ratio
        latency_ratio = interval / self.policy.current_interval

        # Calculate stress score
        stress = calculate_stress(
            load_avg=sys_metrics.load_avg,
            core_count=self.core_count,
            mem_available_pct=mem_pct,
            throttled=pm_result.throttled,
            latency_ratio=latency_ratio,
            io_rate=io_rate,
            io_baseline=int(self.io_baseline.baseline_fast),
        )

        # Create sample
        sample = Sample(
            timestamp=now,
            interval=interval,
            cpu_pct=pm_result.cpu_pct,
            load_avg=sys_metrics.load_avg,
            mem_available=sys_metrics.mem_available,
            swap_used=sys_metrics.swap_used,
            io_read=sys_metrics.io_read,
            io_write=sys_metrics.io_write,
            net_sent=sys_metrics.net_sent,
            net_recv=sys_metrics.net_recv,
            cpu_temp=pm_result.cpu_temp,
            cpu_freq=pm_result.cpu_freq,
            throttled=pm_result.throttled,
            gpu_pct=pm_result.gpu_pct,
            stress=stress,
        )

        # Store sample
        if self._conn:
            insert_sample(self._conn, sample)

        # Update state
        self.state.update_sample(stress.total, now)

        # Update policy and handle state changes
        policy_result = self.policy.update(stress)
        await self._handle_policy_result(policy_result, stress)

        return sample

    async def _check_for_pause(self, actual_interval: float) -> None:
        """Check if the interval indicates a system pause."""
        recent_wake = was_recently_asleep(within_seconds=actual_interval)
        pause = self.pause_detector.check(actual_interval, recent_wake)

        if pause:
            await self._handle_pause(pause)

    async def _handle_pause(self, pause: PauseEvent) -> None:
        """Handle a detected pause event."""
        log.warning(
            "pause_detected",
            duration=pause.duration,
            latency_ratio=pause.latency_ratio,
        )

        # Create forensics capture
        event_dir = create_event_dir(self.config.events_dir, pause.timestamp)
        capture = ForensicsCapture(event_dir)

        # Write metadata
        capture.write_metadata(
            {
                "timestamp": pause.timestamp.isoformat(),
                "duration": pause.duration,
                "expected_interval": pause.expected,
                "latency_ratio": pause.latency_ratio,
            }
        )

        # Run forensics capture in background
        asyncio.create_task(self._run_forensics(capture))

        # Create event record
        event = Event(
            timestamp=pause.timestamp,
            duration=pause.duration,
            stress=StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0),
            culprits=[],
            event_dir=str(event_dir),
            notes=None,
        )

        if self._conn:
            insert_event(self._conn, event)

        self.state.event_count += 1

        # Send notification
        self.notifier.pause_detected(pause.duration, event_dir)

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

    async def _handle_policy_result(self, result: PolicyResult, stress: StressBreakdown) -> None:
        """Handle policy state changes."""
        if result.state_changed:
            if self.policy.state == SamplingState.ELEVATED:
                self.state.enter_elevated()
                self.notifier.elevated_entered(stress.total)
            else:
                self.state.exit_elevated()

        # Track critical stress
        if stress.total >= self.config.sampling.critical_threshold:
            self.state.enter_critical()
            if self.state.critical_duration >= self.config.alerts.critical_duration:
                self.notifier.critical_stress(
                    stress.total,
                    self.state.critical_duration,
                )
        else:
            self.state.exit_critical()

        # Trigger preemptive snapshot if requested
        if result.should_snapshot:
            log.info("preemptive_snapshot_triggered", stress=stress.total)
            event_dir = create_event_dir(
                self.config.events_dir,
                datetime.now(),
            )
            capture = ForensicsCapture(event_dir)
            asyncio.create_task(self._run_forensics(capture))


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
