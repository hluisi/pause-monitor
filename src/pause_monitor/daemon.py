"""Background daemon for pause-monitor."""

import asyncio
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from pause_monitor.collector import PowermetricsResult, PowermetricsStream, get_core_count
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
    prune_old_data,
)
from pause_monitor.stress import (
    IOBaselineManager,
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

        # Tier 2 peak tracking
        self._tier2_entry_time: float | None = None
        self._tier2_peak_stress: int = 0
        self._tier2_peak_breakdown: StressBreakdown | None = None
        self._tier2_peak_process: str | None = None
        self._tier2_peak_pagein_process: str | None = None

        # Peak tracking timer
        self._last_peak_check: float = 0.0

        # Latest powermetrics result (for peak tracking process extraction)
        self._latest_pm_result: PowermetricsResult | None = None

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
        self._auto_prune_task: asyncio.Task | None = None
        self._powermetrics: PowermetricsStream | None = None

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
        log.info("daemon_starting")

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

        Args:
            contents: Frozen ring buffer contents
            duration: Pause duration in seconds
        """
        # Create event directory
        timestamp = datetime.now()
        event_dir = self.config.events_dir / timestamp.strftime("%Y%m%d_%H%M%S")
        event_dir.mkdir(parents=True, exist_ok=True)

        # Identify culprits from ring buffer using powermetrics data
        # identify_culprits returns [{"factor": str, "score": int, "processes": [str]}]
        culprits = identify_culprits(contents)
        # Flatten process lists from top factors, dedupe, keep top 5
        all_procs = [p for c in culprits for p in c.get("processes", [])]
        culprit_names = list(dict.fromkeys(all_procs))[:5]

        # Create capture context
        capture = ForensicsCapture(event_dir)

        # Write ring buffer data
        capture.write_ring_buffer(contents)

        # Find peak sample
        peak_sample = (
            max(contents.samples, key=lambda s: s.stress.total) if contents.samples else None
        )
        peak_stress = peak_sample.stress.total if peak_sample else 0

        # Write metadata
        capture.write_metadata(
            {
                "timestamp": timestamp.isoformat(),
                "peak_stress": peak_stress,
                "culprits": culprit_names,
                "sample_count": len(contents.samples),
            }
        )

        # Run heavy captures (spindump, tailspin, logs) in background
        asyncio.create_task(
            run_full_capture(capture, window_seconds=self.config.sentinel.ring_buffer_seconds)
        )

        # Write event to database
        event = Event(
            timestamp=timestamp,
            duration=duration,
            stress=peak_sample.stress if peak_sample else StressBreakdown(0, 0, 0, 0, 0, 0, 0, 0),
            culprits=culprit_names,
            event_dir=str(event_dir),
            status="unreviewed",
            peak_stress=peak_stress,
        )
        insert_event(self._conn, event)
        self.state.event_count += 1

        # Notify user
        self.notifier.pause_detected(duration=duration, event_dir=event_dir)

        log.info("forensics_started", event_dir=str(event_dir), culprits=culprit_names)

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

    async def _handle_tier_action(self, action: TierAction, stress: StressBreakdown) -> None:
        """Handle tier transition actions.

        Requires: _init_database() must have been called (self._conn must be set).
        """
        if action == TierAction.TIER2_ENTRY:
            self._tier2_entry_time = time.monotonic()
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress
            self.ring_buffer.snapshot_processes(trigger=action.value)
            log.info(
                "tier2_entered",
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
            self.ring_buffer.snapshot_processes(trigger=action.value)
            log.info(
                "tier2_new_peak",
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
            # Read entry time from Daemon (tracked at entry)
            entry_time = self._tier2_entry_time
            if entry_time is not None:
                duration = time.monotonic() - entry_time
                # Compute wall-clock entry time from duration
                entry_timestamp = datetime.now() - timedelta(seconds=duration)
                event = Event(
                    timestamp=entry_timestamp,
                    duration=duration,
                    stress=self._tier2_peak_breakdown or stress,
                    culprits=[],  # Populated from ring buffer snapshot
                    event_dir=None,  # Bookmarks don't have forensics
                    status="unreviewed",
                    peak_stress=self._tier2_peak_stress,
                )
                insert_event(self._conn, event)
                self.state.event_count += 1
                log.info("tier2_exited", duration=duration, peak=self._tier2_peak_stress)

            self._tier2_entry_time = None
            self._tier2_peak_stress = 0
            self._tier2_peak_breakdown = None
            self._tier2_peak_process = None
            self.ring_buffer.clear_snapshots()

        elif action == TierAction.TIER3_ENTRY:
            self.ring_buffer.snapshot_processes(trigger=action.value)
            log.warning(
                "tier3_entered",
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
            # De-escalating to tier 2 - TierManager handles entry time tracking
            # Peak tracking starts fresh for recovery period
            self._tier2_peak_stress = stress.total
            self._tier2_peak_breakdown = stress
            log.info("tier3_exited", stress=stress.total)

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

    async def _handle_pause_from_sentinel(
        self,
        actual: float,
        expected: float,
        contents: BufferContents,
    ) -> None:
        """Handle pause detection from sentinel.

        NOTE: This method will be removed in Task 3.6-3.7 when the main loop
        replaces Sentinel. Use _handle_pause for new code paths.

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
        asyncio.create_task(self._run_heavy_capture(capture))

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
                last_sample_time = time.monotonic()

                async for pm_result in self._powermetrics.read_samples():
                    if self._shutdown_event.is_set():
                        break

                    # Measure actual interval for latency/pause detection
                    now = time.monotonic()
                    actual_interval = now - last_sample_time
                    last_sample_time = now
                    latency_ratio = actual_interval / expected_interval

                    # Store latest powermetrics result for peak tracking
                    self._latest_pm_result = pm_result

                    # Calculate stress from powermetrics data
                    stress = self._calculate_stress(pm_result, latency_ratio)

                    # Get current tier for the sample
                    current_tier = self.tier_manager.current_tier

                    # Push to ring buffer (Phase 1: includes raw metrics for forensics)
                    self.ring_buffer.push(pm_result, stress, tier=current_tier)

                    # Push to socket for TUI (push-based streaming)
                    socket_server = getattr(self, "_socket_server", None)
                    if socket_server and socket_server.has_clients:
                        await socket_server.broadcast(pm_result, stress, current_tier)

                    # Update tier manager and handle transitions
                    action = self.tier_manager.update(stress.total)
                    if action:
                        await self._handle_tier_action(action, stress)

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
                await asyncio.sleep(1.0)  # Wait before restart
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
