"""Background daemon for pause-monitor."""

import asyncio
import os
import resource
import signal
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import structlog
from termcolor import colored

from pause_monitor.boottime import get_boot_time
from pause_monitor.collector import TopCollector
from pause_monitor.config import Config
from pause_monitor.forensics import (
    ForensicsCapture,
    run_full_capture,
)
from pause_monitor.notifications import Notifier
from pause_monitor.ringbuffer import BufferContents, RingBuffer
from pause_monitor.sleepwake import was_recently_asleep
from pause_monitor.socket_server import SocketServer
from pause_monitor.storage import (
    init_database,
    prune_old_data,
)
from pause_monitor.tracker import ProcessTracker

log = structlog.get_logger()


@dataclass
class DaemonState:
    """Runtime state of the daemon."""

    running: bool = False
    sample_count: int = 0
    event_count: int = 0
    last_sample_time: datetime | None = None
    current_score: int = 0

    def update_sample(self, score: int) -> None:
        """Update state after a sample."""
        self.sample_count += 1
        self.current_score = score
        self.last_sample_time = datetime.now()


class Daemon:
    """Main daemon class orchestrating sampling and detection."""

    def __init__(self, config: Config):
        self.config = config
        self.state = DaemonState()

        self.notifier = Notifier(config.alerts)

        # TopCollector for per-process sampling at 1Hz
        self.collector = TopCollector(config)

        # Initialize ring buffer: ring_buffer_seconds samples (1Hz loop)
        max_samples = config.sentinel.ring_buffer_seconds
        self.ring_buffer = RingBuffer(max_samples=max_samples)

        # Boot time for process tracking (stable across daemon restarts)
        self.boot_time = get_boot_time()

        # Database connection and tracker. Initialized eagerly if DB exists and has
        # compatible schema; deferred to _init_database() otherwise.
        self._conn: sqlite3.Connection | None = None
        self.tracker: ProcessTracker | None = None
        if config.db_path.exists():
            try:
                self._conn = sqlite3.connect(config.db_path)
                self.tracker = ProcessTracker(self._conn, config.bands, self.boot_time)
            except sqlite3.OperationalError:
                # DB exists but wrong schema; will be recreated in _init_database
                if self._conn:
                    self._conn.close()
                    self._conn = None

        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
        self._auto_prune_task: asyncio.Task | None = None
        self._socket_server: SocketServer | None = None

    async def _init_database(self) -> None:
        """Initialize database connection.

        Extracted from start() so tests can initialize DB without full daemon startup.
        No migrations - if schema version mismatches, init_database() deletes and recreates.
        """
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        db_existed = self.config.db_path.exists()
        init_database(self.config.db_path)  # Handles version check + recreate

        # Create connection and tracker if not already initialized in __init__
        if self._conn is None:
            self._conn = sqlite3.connect(self.config.db_path)
        if self.tracker is None:
            self.tracker = ProcessTracker(self._conn, self.config.bands, self.boot_time)

        # Log database state
        restored_count = len(self.tracker.tracked) if self.tracker else 0
        log.info(
            "database_ready",
            existed=db_existed,
            restored_tracking=restored_count,
        )

    async def start(self) -> None:
        """Start the daemon."""
        from importlib.metadata import version

        log.info("daemon_starting", version=version("pause-monitor"))

        # Log configuration for visibility
        bands = self.config.bands
        log.info(
            "daemon_config",
            sample_interval_ms=self.config.sentinel.sample_interval_ms,
            pause_threshold_ratio=self.config.sentinel.pause_threshold_ratio,
            ring_buffer_seconds=self.config.sentinel.ring_buffer_seconds,
            tracking_threshold=bands.tracking_threshold,
            tracking_band=bands.tracking_band,
            band_thresholds=f"low={bands.low}/med={bands.medium}/elev={bands.elevated}/high={bands.high}/crit={bands.critical}",
            pause_min_duration=self.config.alerts.pause_min_duration,
        )
        log.info("daemon_boot_time", boot_time=self.boot_time)

        # Set QoS to USER_INITIATED for reliable sampling under load
        # Ensures we get CPU time even when system is busy (when monitoring matters most)
        try:
            os.setpriority(os.PRIO_PROCESS, 0, -10)  # Negative nice = higher priority
            log.info("daemon_priority_set", nice=-10)
        except PermissionError:
            log.warning("qos_priority_failed", msg="Could not set high priority, running as normal")

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
        await self._init_database()

        # Start caffeinate to prevent App Nap
        await self._start_caffeinate()

        # Start socket server for TUI communication
        self._socket_server = SocketServer(
            socket_path=self.config.socket_path,
            ring_buffer=self.ring_buffer,
        )
        await self._socket_server.start()

        self.state.running = True
        log.info("daemon_started")

        # Start auto-prune task
        self._auto_prune_task = asyncio.create_task(self._auto_prune())

        # Run main loop (TopCollector -> stress -> ring buffer -> tiers)
        await self._main_loop()

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        log.info("daemon_stopping")
        self.state.running = False

        # Stop socket server
        if self._socket_server:
            await self._socket_server.stop()
            self._socket_server = None

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

        # Close database and tracker
        if self._conn:
            self._conn.close()
            self._conn = None
        self.tracker = None

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
            try:
                self._caffeinate_proc.terminate()
                await asyncio.wait_for(self._caffeinate_proc.wait(), timeout=5.0)
            except ProcessLookupError:
                pass  # Process already exited
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
                        events_days=self.config.retention.events_days,
                    )
                    log.info("auto_prune_completed", events_deleted=deleted)

    async def _run_heavy_capture(self, capture: ForensicsCapture) -> None:
        """Run heavy forensics capture (spindump, tailspin, logs) and notify on completion."""
        try:
            await run_full_capture(capture, config=self.config.forensics)
            self.notifier.forensics_completed(capture.event_dir)
        except Exception as e:
            log.exception(
                "forensics_capture_failed",
                event_dir=str(capture.event_dir),
                error=str(e),
            )

    async def _run_forensics(self, contents: BufferContents, *, duration: float) -> None:
        """Run full forensics capture.

        Captures forensics data (ring buffer, spindump, tailspin, logs) for pause events.

        Args:
            contents: Frozen ring buffer contents
            duration: Pause duration in seconds
        """
        # Create event directory
        timestamp = datetime.now()
        event_dir = self.config.events_dir / timestamp.strftime("%Y%m%d_%H%M%S")
        event_dir.mkdir(parents=True, exist_ok=True)

        # Create capture context
        capture = ForensicsCapture(event_dir)

        # Write ring buffer data
        capture.write_ring_buffer(contents)

        # Find peak sample for metadata (by max_score)
        peak_sample = (
            max(contents.samples, key=lambda s: s.samples.max_score) if contents.samples else None
        )
        peak_score = peak_sample.samples.max_score if peak_sample else 0

        # Extract top process names from peak sample's rogues
        culprit_names = []
        if peak_sample and peak_sample.samples.rogues:
            for rogue in peak_sample.samples.rogues:
                if rogue.command and rogue.command not in culprit_names:
                    culprit_names.append(rogue.command)

        # Write metadata
        capture.write_metadata(
            {
                "timestamp": timestamp.isoformat(),
                "duration": duration,
                "peak_score": peak_score,
                "culprits": culprit_names,
                "sample_count": len(contents.samples),
            }
        )

        # Run heavy captures (spindump, tailspin, logs) in background
        asyncio.create_task(
            run_full_capture(
                capture,
                window_seconds=self.config.sentinel.ring_buffer_seconds,
                config=self.config.forensics,
            )
        )

        # Notify user
        self.notifier.pause_detected(duration=duration, event_dir=event_dir)

        log.info("forensics_started", event_dir=str(event_dir), culprits=culprit_names)

    async def _handle_pause(self, elapsed_ms: int, expected_ms: int) -> None:
        """Handle detected pause - run full forensics.

        A pause is when our loop was delayed >threshold (system was frozen).

        Args:
            elapsed_ms: How long the sample actually took (ms)
            expected_ms: How long it should have taken (ms)
        """
        # Convert to seconds for compatibility
        elapsed_sec = elapsed_ms / 1000.0

        # Check if we just woke from sleep (not a real pause)
        if was_recently_asleep(within_seconds=elapsed_sec):
            log.info("pause_was_sleep_wake", elapsed_ms=elapsed_ms)
            return

        # Check minimum duration threshold
        if elapsed_sec < self.config.alerts.pause_min_duration:
            log.debug(
                "pause_below_threshold",
                elapsed_ms=elapsed_ms,
                min_duration=self.config.alerts.pause_min_duration,
            )
            return

        log.warning(
            "pause_detected",
            elapsed_ms=elapsed_ms,
            expected_ms=expected_ms,
            ratio=elapsed_ms / expected_ms if expected_ms > 0 else 0,
        )

        # Freeze ring buffer (immutable snapshot)
        contents = self.ring_buffer.freeze()

        # Run forensics in background
        await self._run_forensics(contents, duration=elapsed_sec)

    async def _main_loop(self) -> None:
        """Main 1Hz loop collecting process samples.

        Each iteration:
        1. Collect samples via TopCollector
        2. Update per-process tracking with rogue processes
        3. Push to ring buffer
        4. Check for pause (elapsed_ms > threshold)
        5. Broadcast to socket for TUI

        The loop runs until shutdown event is set.
        """
        expected_ms = self.config.sentinel.sample_interval_ms
        pause_threshold = self.config.sentinel.pause_threshold_ratio

        # Heartbeat tracking (log every 60 samples = ~1 minute)
        heartbeat_interval = 60
        heartbeat_count = 0
        heartbeat_max_score = 0
        heartbeat_score_sum = 0

        # Near-miss threshold: log when ratio exceeds this but is below pause_threshold
        near_miss_ratio = 2.0

        # Track rogue selection churn (PIDs entering/leaving selection)
        previous_rogues: dict[int, str] = {}  # pid -> command

        while not self._shutdown_event.is_set():
            try:
                # Collect samples (this takes ~1 second due to top -l 2)
                samples = await self.collector.collect()

                if self._shutdown_event.is_set():
                    break

                # Log rogue selection churn (new/exited processes)
                current_rogues = {r.pid: r.command for r in samples.rogues}
                current_pids = set(current_rogues.keys())
                previous_pids = set(previous_rogues.keys())

                # New processes entering rogue selection
                for pid in current_pids - previous_pids:
                    rogue = next(r for r in samples.rogues if r.pid == pid)
                    cats = ",".join(sorted(rogue.categories))
                    msg = (
                        f"rogue_entered: {colored(rogue.command, 'cyan')} "
                        f"{colored(f'({rogue.score})', 'yellow')} "
                        f"{colored(f'pid={pid}', 'dark_grey')} "
                        f"{colored(f'[{cats}]', 'blue')}"
                    )
                    log.info(msg)

                # Processes exiting rogue selection
                for pid in previous_pids - current_pids:
                    msg = (
                        f"rogue_exited: {colored(previous_rogues[pid], 'cyan')} "
                        f"{colored(f'pid={pid}', 'dark_grey')}"
                    )
                    log.info(msg)

                previous_rogues = current_rogues

                # Update per-process tracking
                if self.tracker is not None:
                    self.tracker.update(samples.rogues)

                # Push to ring buffer
                self.ring_buffer.push(samples)

                # Log elevated samples for visibility between heartbeats
                elevated_threshold = self.config.bands.elevated
                if samples.max_score >= elevated_threshold and samples.rogues:
                    top = samples.rogues[0]
                    msg = (
                        f"elevated_sample: {colored(top.command, 'cyan')} "
                        f"{colored(f'({samples.max_score})', 'yellow')} "
                        f"{colored(f'pid={top.pid}', 'dark_grey')}"
                    )
                    log.info(msg)

                # Update heartbeat stats
                heartbeat_count += 1
                heartbeat_score_sum += samples.max_score
                heartbeat_max_score = max(heartbeat_max_score, samples.max_score)

                # Calculate timing ratio for pause detection
                timing_ratio = samples.elapsed_ms / expected_ms if expected_ms > 0 else 0

                # Check for pause (elapsed_ms much larger than expected)
                if timing_ratio > pause_threshold:
                    await self._handle_pause(samples.elapsed_ms, expected_ms)
                elif timing_ratio > near_miss_ratio:
                    # Near-miss: elevated timing but below pause threshold
                    log.info(
                        "pause_near_miss",
                        elapsed_ms=samples.elapsed_ms,
                        expected_ms=expected_ms,
                        ratio=round(timing_ratio, 2),
                        threshold=pause_threshold,
                    )

                # Broadcast to TUI clients
                if self._socket_server and self._socket_server.has_clients:
                    await self._socket_server.broadcast(samples)

                # Update daemon state
                self.state.update_sample(samples.max_score)

                # Periodic heartbeat log
                if heartbeat_count >= heartbeat_interval:
                    tracked_count = len(self.tracker.tracked) if self.tracker else 0
                    client_count = len(self._socket_server._clients) if self._socket_server else 0
                    buffer_size = len(self.ring_buffer)

                    # Resource monitoring
                    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
                    db_size_mb = (
                        self.config.db_path.stat().st_size / 1024 / 1024
                        if self.config.db_path.exists()
                        else 0
                    )

                    log.info(
                        "daemon_heartbeat",
                        samples=heartbeat_count,
                        max_score=heartbeat_max_score,
                        avg_score=round(heartbeat_score_sum / heartbeat_count),
                        tracked=tracked_count,
                        buffer=f"{buffer_size}/{self.ring_buffer.capacity}",
                        clients=client_count,
                        rss_mb=round(rss_mb, 1),
                        db_mb=round(db_size_mb, 1),
                    )

                    # Reset heartbeat counters
                    heartbeat_count = 0
                    heartbeat_max_score = 0
                    heartbeat_score_sum = 0

            except asyncio.CancelledError:
                log.info("main_loop_cancelled")
                break
            except Exception as e:
                log.error("sample_failed", error=str(e))
                # Wait briefly before retry, but exit immediately if shutdown
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    pass  # Continue with next sample  # Continue with next sample


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
