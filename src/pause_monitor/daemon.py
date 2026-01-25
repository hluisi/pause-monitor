"""Background daemon for pause-monitor."""

import asyncio
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.collector import ProcessSamples, TopCollector
from pause_monitor.config import Config
from pause_monitor.forensics import (
    ForensicsCapture,
    run_full_capture,
)
from pause_monitor.notifications import Notifier
from pause_monitor.ringbuffer import BufferContents, RingBuffer
from pause_monitor.sentinel import TierAction, TierManager
from pause_monitor.sleepwake import was_recently_asleep
from pause_monitor.socket_server import SocketServer
from pause_monitor.storage import (
    create_event,
    finalize_event,
    init_database,
    insert_process_sample,
    prune_old_data,
)

log = structlog.get_logger()


@dataclass
class DaemonState:
    """Runtime state of the daemon."""

    running: bool = False
    sample_count: int = 0
    event_count: int = 0
    last_sample_time: datetime | None = None
    current_score: int = 0
    elevated_since: datetime | None = None
    critical_since: datetime | None = None

    def update_sample(self, score: int) -> None:
        """Update state after a sample."""
        self.sample_count += 1
        self.current_score = score
        self.last_sample_time = datetime.now()

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

        # TopCollector for per-process sampling at 1Hz
        self.collector = TopCollector(config)

        # Initialize ring buffer: ring_buffer_seconds samples (1Hz loop)
        max_samples = config.sentinel.ring_buffer_seconds
        self.ring_buffer = RingBuffer(max_samples=max_samples)

        # Tier management
        self.tier_manager = TierManager(
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )

        # Event tracking (tier-based saving)
        self._current_event_id: int | None = None  # Active event ID (tier 2+)
        self._current_peak_tier: int = 1  # Highest tier reached in current event
        self._current_peak_score: int = 0  # Peak score in current event

        # Entry time tracking (for notifications)
        self._tier2_entry_time: float | None = None

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
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
        init_database(self.config.db_path)  # Handles version check + recreate
        self._conn = sqlite3.connect(self.config.db_path)

    async def start(self) -> None:
        """Start the daemon."""
        from importlib.metadata import version

        log.info("daemon_starting", version=version("pause-monitor"))

        # Set QoS to USER_INITIATED for reliable sampling under load
        # Ensures we get CPU time even when system is busy (when monitoring matters most)
        try:
            os.setpriority(os.PRIO_PROCESS, 0, -10)  # Negative nice = higher priority
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
                        samples_days=self.config.retention.samples_days,
                        events_days=self.config.retention.events_days,
                    )
                    log.info("auto_prune_completed", samples=deleted[0], events=deleted[1])

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

        Note: This does NOT create an event - events are created by tier transitions.
        This just captures forensics data (ring buffer, spindump, tailspin, logs).

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
                "tier": self.tier_manager.current_tier,
                "event_id": self._current_event_id,
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

    # === Tier Callbacks ===

    async def _handle_tier_change(self, action: TierAction, tier: int) -> None:
        """Handle tier state changes from TierManager.

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
            self.notifier.elevated_entered(self.tier_manager.peak_score)
        elif action == TierAction.TIER3_ENTRY:
            # Critical score notification
            self.notifier.critical_stress(
                self.tier_manager.peak_score,
                0.0,  # Just entered, no duration yet
            )

    def _save_event_sample(self, samples: ProcessSamples, tier: int) -> None:
        """Save a sample to the current event.

        Args:
            samples: Current ProcessSamples with rogues
            tier: Current tier (2 or 3)
        """
        if self._current_event_id is None or self._conn is None:
            return

        record_id = insert_process_sample(
            self._conn,
            self._current_event_id,
            tier,
            samples,
        )

        log.debug(
            "event_sample_saved",
            event_id=self._current_event_id,
            record_id=record_id,
            tier=tier,
            max_score=samples.max_score,
            rogue_count=len(samples.rogues),
        )

    async def _handle_tier_action(self, action: TierAction, samples: ProcessSamples) -> None:
        """Handle tier transition actions.

        Requires: _init_database() must have been called (self._conn must be set).

        Args:
            action: The tier action to handle
            samples: Current ProcessSamples with rogues and max_score
        """
        score = samples.max_score

        # Handle state updates and notifications for tier changes
        await self._handle_tier_change(action, self.tier_manager.current_tier)

        if action == TierAction.TIER2_ENTRY:
            # Create new event on first escalation from tier 1
            self._current_event_id = create_event(self._conn, datetime.now())
            self._current_peak_tier = 2
            self._current_peak_score = score
            self._tier2_entry_time = time.monotonic()

            # Save entry sample
            self._save_event_sample(samples, tier=2)

            log.info(
                "tier2_entered",
                event_id=self._current_event_id,
                score=score,
                rogue_count=len(samples.rogues),
                top_rogue=samples.rogues[0].command if samples.rogues else None,
            )

        elif action == TierAction.TIER2_PEAK:
            if score > self._current_peak_score:
                self._current_peak_score = score

            # Save peak sample
            self._save_event_sample(samples, tier=2)

            log.info(
                "tier2_new_peak",
                event_id=self._current_event_id,
                score=score,
                rogue_count=len(samples.rogues),
                top_rogue=samples.rogues[0].command if samples.rogues else None,
            )

        elif action == TierAction.TIER2_EXIT:
            # Finalize event when returning to tier 1
            if self._current_event_id is not None:
                finalize_event(
                    self._conn,
                    self._current_event_id,
                    end_timestamp=datetime.now(),
                    peak_stress=self._current_peak_score,  # Field name kept for compatibility
                    peak_tier=self._current_peak_tier,
                )
                self.state.event_count += 1

                entry_time = self._tier2_entry_time
                duration = time.monotonic() - entry_time if entry_time else 0.0

                log.info(
                    "tier2_exited",
                    event_id=self._current_event_id,
                    duration=duration,
                    peak_score=self._current_peak_score,
                    peak_tier=self._current_peak_tier,
                )

            # Reset event tracking
            self._current_event_id = None
            self._current_peak_tier = 1
            self._current_peak_score = 0
            self._tier2_entry_time = None

        elif action == TierAction.TIER3_ENTRY:
            # If entering tier 3 directly from tier 1, create event first
            if self._current_event_id is None:
                self._current_event_id = create_event(self._conn, datetime.now())
                self._tier2_entry_time = time.monotonic()

            self._current_peak_tier = 3
            if score > self._current_peak_score:
                self._current_peak_score = score

            # Save entry sample
            self._save_event_sample(samples, tier=3)

            log.warning(
                "tier3_entered",
                event_id=self._current_event_id,
                score=score,
                rogue_count=len(samples.rogues),
                top_rogue=samples.rogues[0].command if samples.rogues else None,
            )

        elif action == TierAction.TIER3_EXIT:
            # De-escalating to tier 2 - event continues
            log.info("tier3_exited", event_id=self._current_event_id, score=score)

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
        2. Push to ring buffer
        3. Update tier manager with max_score
        4. Handle tier transitions
        5. Check for pause (elapsed_ms > threshold)
        6. Broadcast to socket for TUI

        The loop runs until shutdown event is set.
        """
        expected_ms = self.config.sentinel.sample_interval_ms
        pause_threshold = self.config.sentinel.pause_threshold_ratio

        while not self._shutdown_event.is_set():
            try:
                # Collect samples (this takes ~1 second due to top -l 2)
                samples = await self.collector.collect()

                if self._shutdown_event.is_set():
                    break

                # Get current tier for the sample
                tier = self.tier_manager.current_tier

                # Push to ring buffer
                self.ring_buffer.push(samples, tier)

                # Update tier manager with max score
                action = self.tier_manager.update(samples.max_score)
                if action:
                    await self._handle_tier_action(action, samples)

                # Tier 3 continuous saving (every sample)
                tier = self.tier_manager.current_tier
                if tier == 3 and action != TierAction.TIER3_ENTRY:
                    # TIER3_ENTRY already saves, avoid duplicate
                    self._save_event_sample(samples, tier=3)

                # Check for pause (elapsed_ms much larger than expected)
                if samples.elapsed_ms > expected_ms * pause_threshold:
                    await self._handle_pause(samples.elapsed_ms, expected_ms)

                # Broadcast to TUI clients
                if self._socket_server and self._socket_server.has_clients:
                    await self._socket_server.broadcast(samples, tier)

                # Update daemon state
                self.state.update_sample(samples.max_score)

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
                    pass  # Continue with next sample


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
