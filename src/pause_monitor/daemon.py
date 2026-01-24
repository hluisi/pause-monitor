"""Background daemon for pause-monitor."""

import asyncio
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.collector import PowermetricsResult, PowermetricsStream, get_core_count
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
    EventSample,
    create_event,
    finalize_event,
    init_database,
    insert_event_sample,
    prune_old_data,
)
from pause_monitor.stress import (
    StressBreakdown,
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

        self.notifier = Notifier(config.alerts)
        self.core_count = get_core_count()

        # Initialize ring buffer: ring_buffer_seconds * 10 samples (10Hz fast loop)
        max_samples = config.sentinel.ring_buffer_seconds * 10
        self.ring_buffer = RingBuffer(max_samples=max_samples)

        # Tier management
        self.tier_manager = TierManager(
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )

        # Event tracking (tier-based saving)
        self._current_event_id: int | None = None  # Active event ID (tier 2+)
        self._current_peak_tier: int = 1  # Highest tier reached in current event
        self._current_peak_stress: int = 0  # Peak stress in current event

        # Legacy peak tracking (for notifications)
        self._tier2_entry_time: float | None = None
        self._tier2_peak_stress: int = 0
        self._tier2_peak_breakdown: StressBreakdown | None = None
        self._tier2_peak_process: str | None = None
        self._tier2_peak_pagein_process: str | None = None

        # Peak tracking timer
        self._last_peak_check: float = 0.0

        # Latest powermetrics result (for peak tracking process extraction)
        self._latest_pm_result: PowermetricsResult | None = None

        # Latest system metrics (updated during stress calculation, used for TUI broadcast)
        self._latest_load_avg: float = 0.0
        self._latest_mem_pressure: int = 0

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
        self._auto_prune_task: asyncio.Task | None = None
        self._powermetrics: PowermetricsStream | None = None
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

        # Run main loop (powermetrics -> stress -> ring buffer -> tiers)
        # This replaces the old sentinel.start() call
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

        # Terminate powermetrics to unblock the read loop
        if self._powermetrics:
            self._powermetrics.terminate()  # Already dead

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
            await run_full_capture(capture)
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

        # Find peak sample for metadata
        peak_sample = (
            max(contents.samples, key=lambda s: s.stress.total) if contents.samples else None
        )
        peak_stress = peak_sample.stress.total if peak_sample else 0

        # Extract top process names from peak sample for metadata
        culprit_names = []
        if peak_sample and peak_sample.metrics:
            # Get names from top CPU processes
            for proc in peak_sample.metrics.top_cpu_processes[:5]:
                if proc.get("name") and proc["name"] not in culprit_names:
                    culprit_names.append(proc["name"])

        # Write metadata
        capture.write_metadata(
            {
                "timestamp": timestamp.isoformat(),
                "duration": duration,
                "peak_stress": peak_stress,
                "culprits": culprit_names,
                "sample_count": len(contents.samples),
                "tier": self.tier_manager.current_tier,
                "event_id": self._current_event_id,
            }
        )

        # Run heavy captures (spindump, tailspin, logs) in background
        asyncio.create_task(
            run_full_capture(capture, window_seconds=self.config.sentinel.ring_buffer_seconds)
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

    def _save_event_sample(
        self, metrics: PowermetricsResult, stress: StressBreakdown, tier: int
    ) -> None:
        """Save a sample to the current event.

        Args:
            metrics: Current PowermetricsResult
            stress: Current stress breakdown
            tier: Current tier (2 or 3)
        """
        if self._current_event_id is None or self._conn is None:
            return

        sample = EventSample(
            event_id=self._current_event_id,
            timestamp=datetime.now(),
            tier=tier,
            elapsed_ns=metrics.elapsed_ns,
            throttled=metrics.throttled,
            cpu_power=metrics.cpu_power,
            gpu_pct=metrics.gpu_pct,
            gpu_power=metrics.gpu_power,
            io_read_per_s=metrics.io_read_per_s,
            io_write_per_s=metrics.io_write_per_s,
            wakeups_per_s=metrics.wakeups_per_s,
            pageins_per_s=metrics.pageins_per_s,
            stress=stress,
            top_cpu_procs=metrics.top_cpu_processes,
            top_pagein_procs=metrics.top_pagein_processes,
            top_wakeup_procs=metrics.top_wakeup_processes,
            top_diskio_procs=metrics.top_diskio_processes,
        )
        insert_event_sample(self._conn, sample)

    async def _handle_tier_action(
        self, action: TierAction, stress: StressBreakdown, metrics: PowermetricsResult | None = None
    ) -> None:
        """Handle tier transition actions.

        Requires: _init_database() must have been called (self._conn must be set).
        """
        if action == TierAction.TIER2_ENTRY:
            # Create new event on first escalation from tier 1
            self._current_event_id = create_event(self._conn, datetime.now())
            self._current_peak_tier = 2
            self._current_peak_stress = stress.total
            self._tier2_entry_time = time.monotonic()
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress

            # Save entry sample
            if metrics:
                self._save_event_sample(metrics, stress, tier=2)

            log.info(
                "tier2_entered",
                event_id=self._current_event_id,
                stress=stress.total,
                load=stress.load,
                memory=stress.memory,
                thermal=stress.thermal,
                latency=stress.latency,
                io=stress.io,
                gpu=stress.gpu,
                wakeups=stress.wakeups,
                pageins=stress.pageins,
            )

        elif action == TierAction.TIER2_PEAK:
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress
            if stress.total > self._current_peak_stress:
                self._current_peak_stress = stress.total

            # Save peak sample
            if metrics:
                self._save_event_sample(metrics, stress, tier=2)

            log.info(
                "tier2_new_peak",
                event_id=self._current_event_id,
                stress=stress.total,
                load=stress.load,
                memory=stress.memory,
                thermal=stress.thermal,
                latency=stress.latency,
                io=stress.io,
                gpu=stress.gpu,
                wakeups=stress.wakeups,
                pageins=stress.pageins,
            )

        elif action == TierAction.TIER2_EXIT:
            # Finalize event when returning to tier 1
            if self._current_event_id is not None:
                finalize_event(
                    self._conn,
                    self._current_event_id,
                    end_timestamp=datetime.now(),
                    peak_stress=self._current_peak_stress,
                    peak_tier=self._current_peak_tier,
                )
                self.state.event_count += 1

                entry_time = self._tier2_entry_time
                duration = time.monotonic() - entry_time if entry_time else 0.0

                log.info(
                    "tier2_exited",
                    event_id=self._current_event_id,
                    duration=duration,
                    peak_stress=self._current_peak_stress,
                    peak_tier=self._current_peak_tier,
                )

            # Reset event tracking
            self._current_event_id = None
            self._current_peak_tier = 1
            self._current_peak_stress = 0
            self._tier2_entry_time = None
            self._tier2_peak_stress = 0
            self._tier2_peak_breakdown = None
            self._tier2_peak_process = None

        elif action == TierAction.TIER3_ENTRY:
            # If entering tier 3 directly from tier 1, create event first
            if self._current_event_id is None:
                self._current_event_id = create_event(self._conn, datetime.now())
                self._tier2_entry_time = time.monotonic()

            self._current_peak_tier = 3
            if stress.total > self._current_peak_stress:
                self._current_peak_stress = stress.total

            # Save entry sample
            if metrics:
                self._save_event_sample(metrics, stress, tier=3)

            log.warning(
                "tier3_entered",
                event_id=self._current_event_id,
                stress=stress.total,
                load=stress.load,
                memory=stress.memory,
                thermal=stress.thermal,
                latency=stress.latency,
                io=stress.io,
                gpu=stress.gpu,
                wakeups=stress.wakeups,
                pageins=stress.pageins,
            )

        elif action == TierAction.TIER3_EXIT:
            # De-escalating to tier 2 - event continues
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress
            log.info("tier3_exited", event_id=self._current_event_id, stress=stress.total)

    def _maybe_update_peak(self, stress: StressBreakdown) -> None:
        """Update peak stress if interval has passed and stress is higher.

        This ensures long elevated/critical periods capture the worst moment
        before the ring buffer rolls over.

        Args:
            stress: Current stress breakdown
        """
        now = time.time()
        interval = self.config.sentinel.peak_tracking_seconds

        # Only check periodically
        if now - self._last_peak_check < interval:
            return

        self._last_peak_check = now

        # Update if current stress is higher
        if stress.total > self._tier2_peak_stress:
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress
            # Get top CPU process from latest powermetrics data
            if self._latest_pm_result and self._latest_pm_result.top_cpu_processes:
                self._tier2_peak_process = self._latest_pm_result.top_cpu_processes[0]["name"]
            # Also track top pagein process if any (more likely cause of pauses)
            if self._latest_pm_result and self._latest_pm_result.top_pagein_processes:
                top_pagein = self._latest_pm_result.top_pagein_processes[0]
                self._tier2_peak_pagein_process = top_pagein["name"]
            self.ring_buffer.snapshot_processes(trigger="peak_update")
            log.info("peak_updated", stress=stress.total)

    async def _handle_pause(self, actual_interval: float, expected_interval: float) -> None:
        """Handle detected pause - run full forensics.

        A pause is when our loop was delayed >threshold (system was frozen).

        Args:
            actual_interval: How long the loop actually took
            expected_interval: How long it should have taken
        """
        # Check if we just woke from sleep (not a real pause)
        if was_recently_asleep(within_seconds=actual_interval):
            log.info("pause_was_sleep_wake", actual=actual_interval)
            return

        # Check minimum duration threshold
        if actual_interval < self.config.alerts.pause_min_duration:
            log.debug(
                "pause_below_threshold",
                duration=actual_interval,
                min_duration=self.config.alerts.pause_min_duration,
            )
            return

        log.warning(
            "pause_detected",
            actual=actual_interval,
            expected=expected_interval,
            ratio=actual_interval / expected_interval,
        )

        # Freeze ring buffer (immutable snapshot)
        contents = self.ring_buffer.freeze()

        # Run forensics in background
        await self._run_forensics(contents, duration=actual_interval)

    def _calculate_stress(
        self, pm_result: PowermetricsResult, latency_ratio: float
    ) -> StressBreakdown:
        """Calculate stress breakdown from powermetrics data.

        Args:
            pm_result: Parsed powermetrics sample
            latency_ratio: Actual interval / expected interval (1.0 = on time)

        Returns:
            StressBreakdown with all 8 factors (including pageins - critical for pause detection)
        """
        # Get system metrics
        load_avg = os.getloadavg()[0]
        mem_pressure = get_memory_pressure_fast()

        # Store for TUI broadcast
        self._latest_load_avg = load_avg
        self._latest_mem_pressure = mem_pressure

        # Load stress (0-30 points)
        load_ratio = load_avg / self.core_count if self.core_count > 0 else 0
        if load_ratio < 1.0:
            load = 0
        elif load_ratio < 2.0:
            load = int((load_ratio - 1.0) * 15)  # 0-15 for 1x-2x
        else:
            load = int(min(30, 15 + (load_ratio - 2.0) * 7.5))  # 15-30 for 2x+

        # Memory stress (0-30 points)
        # mem_pressure is "memory free" (0-100, higher = more available)
        # Low free memory = high stress, high free memory = low stress
        mem_used_pct = 100 - mem_pressure  # Convert to "used" perspective
        if mem_used_pct < 70:
            memory = 0  # Under 70% used = no stress
        elif mem_used_pct < 85:
            memory = int((mem_used_pct - 70) * 1.0)  # 0-15 for 70-85% used
        else:
            memory = int(min(30, 15 + (mem_used_pct - 85) * 1.0))  # 15-30 for 85%+ used

        # Thermal stress (0-10 points)
        thermal = 10 if pm_result.throttled else 0

        # Latency stress (0-20 points) - uses config threshold
        pause_threshold = self.config.sentinel.pause_threshold_ratio
        if latency_ratio <= 1.2:
            latency = 0
        elif pause_threshold > 1.2 and latency_ratio <= pause_threshold:
            # Scale from 0-10 between 1.2x and threshold
            # Guard: pause_threshold must be > 1.2 to avoid division by zero
            latency = int((latency_ratio - 1.2) / (pause_threshold - 1.2) * 10)
        else:
            # 10-20 for ratios above threshold
            latency = int(min(20, 10 + (latency_ratio - pause_threshold) * 5))

        # GPU stress (0-20 points)
        gpu = 0
        if pm_result.gpu_pct is not None:
            if pm_result.gpu_pct > 80:
                gpu = int(min(20, (pm_result.gpu_pct - 80) * 1.0))  # 0-20 for 80-100%
            elif pm_result.gpu_pct > 50:
                gpu = int((pm_result.gpu_pct - 50) * 0.33)  # 0-10 for 50-80%

        # Wakeups stress (0-10 points)
        wakeups = 0
        if pm_result.wakeups_per_s > 100:
            wakeups = int(min(10, (pm_result.wakeups_per_s - 100) / 40))  # 100-500 -> 0-10

        # I/O stress (0-10 points)
        # Scale: 0-10 MB/s = 0, 10-100 MB/s = 0-10 points
        # Per Data Dictionary: use io_read_per_s + io_write_per_s
        io = 0
        io_mb_per_sec = (pm_result.io_read_per_s + pm_result.io_write_per_s) / (1024 * 1024)
        if io_mb_per_sec > 10:
            io = int(min(10, (io_mb_per_sec - 10) / 9))  # 10-100 MB/s -> 0-10

        # Pageins stress (0-30 points) - CRITICAL for pause detection
        # Scale: 0-10 pageins/s = 0, 10-100 = 0-15, 100+ = 15-30
        # This is the #1 indicator of user-visible pauses
        pageins = 0
        if pm_result.pageins_per_s > 10:
            if pm_result.pageins_per_s < 100:
                pageins = int((pm_result.pageins_per_s - 10) / 6)  # 10-100 -> 0-15
            else:
                pageins = int(min(30, 15 + (pm_result.pageins_per_s - 100) / 20))  # 100+ -> 15-30

        return StressBreakdown(
            load=load,
            memory=memory,
            thermal=thermal,
            latency=latency,
            io=io,
            gpu=gpu,
            wakeups=wakeups,
            pageins=pageins,
        )

    async def _main_loop(self) -> None:
        """Main loop: process powermetrics samples at 10Hz.

        Each sample:
        1. Measure latency (pause detection)
        2. Calculate stress from powermetrics data
        3. Push to ring buffer
        4. Update tier manager
        5. Handle tier transitions
        6. Periodic peak tracking

        If powermetrics crashes, restart it after 1 second.
        """
        expected_interval = self.config.sentinel.fast_interval_ms / 1000.0
        pause_threshold = self.config.sentinel.pause_threshold_ratio

        while not self._shutdown_event.is_set():
            # (Re)create powermetrics stream if not already set (allows mocking)
            if self._powermetrics is None:
                self._powermetrics = PowermetricsStream(
                    interval_ms=self.config.sentinel.fast_interval_ms
                )

            try:
                await self._powermetrics.start()
                # Don't set last_sample_time until after first sample arrives
                # to avoid counting startup time as latency
                last_sample_time: float | None = None
                is_first_sample = True

                async for pm_result in self._powermetrics.read_samples():
                    if self._shutdown_event.is_set():
                        break

                    # Measure actual interval for latency/pause detection
                    now = time.monotonic()
                    if is_first_sample:
                        # First sample - no latency measurement possible
                        actual_interval = expected_interval  # Assume on-time
                        latency_ratio = 1.0
                        is_first_sample = False
                    else:
                        actual_interval = now - last_sample_time
                        latency_ratio = actual_interval / expected_interval
                    last_sample_time = now

                    # Store latest powermetrics result for peak tracking
                    self._latest_pm_result = pm_result

                    # Calculate stress from powermetrics data
                    stress = self._calculate_stress(pm_result, latency_ratio)

                    # Get current tier for the sample
                    current_tier = self.tier_manager.current_tier

                    # Push to ring buffer (Phase 1: includes raw metrics for forensics)
                    self.ring_buffer.push(pm_result, stress, tier=current_tier)

                    # Push to socket for TUI (push-based streaming)
                    if self._socket_server and self._socket_server.has_clients:
                        await self._socket_server.broadcast(
                            pm_result,
                            stress,
                            current_tier,
                            load_avg=self._latest_load_avg,
                            mem_pressure=self._latest_mem_pressure,
                        )

                    # Update tier manager and handle transitions
                    action = self.tier_manager.update(stress.total)
                    if action:
                        await self._handle_tier_action(action, stress, metrics=pm_result)

                    # Tier 3 continuous saving: save every sample at 10Hz for forensics
                    current_tier = self.tier_manager.current_tier
                    if current_tier == 3 and action != TierAction.TIER3_ENTRY:
                        # TIER3_ENTRY already saves the entry sample, avoid duplicate
                        self._save_event_sample(pm_result, stress, tier=3)

                    # Periodic peak tracking during elevated/critical
                    if current_tier >= 2:
                        self._maybe_update_peak(stress)

                    # Check for pause (latency > threshold)
                    if latency_ratio > pause_threshold:
                        await self._handle_pause(actual_interval, expected_interval)

                    self.state.sample_count += 1

                # Generator ended normally - in production powermetrics runs forever,
                # so this only happens in tests with mocked streams.
                break

            except asyncio.CancelledError:
                log.info("main_loop_cancelled")
                break
            except Exception as e:
                log.error("powermetrics_crashed", error=str(e))
                # Wait 1 second before restart, but exit immediately if shutdown
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
                    break  # Shutdown requested during wait
                except asyncio.TimeoutError:
                    pass  # Timeout expired, continue with restart
            finally:
                if self._powermetrics:
                    await self._powermetrics.stop()


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
