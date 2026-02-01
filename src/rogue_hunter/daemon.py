"""Background daemon for rogue-hunter."""

import asyncio
import ctypes
import logging
import logging.handlers
import os
import resource
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import psutil
import structlog

from rogue_hunter.boottime import get_boot_time
from rogue_hunter.collector import (
    BAND_SEVERITY,
    STATE_SEVERITY,
    LibprocCollector,
    MetricValue,
    MetricValueStr,
    ProcessSamples,
    ProcessScore,
)
from rogue_hunter.config import Config
from rogue_hunter.forensics import ForensicsCapture
from rogue_hunter.ringbuffer import RingBuffer
from rogue_hunter.socket_server import SocketServer
from rogue_hunter.storage import init_database, prune_old_data
from rogue_hunter.tracker import ProcessTracker

log = structlog.get_logger()

# macOS QoS class constants (from pthread/qos.h)
QOS_CLASS_USER_INTERACTIVE = 0x21
QOS_CLASS_USER_INITIATED = 0x19
QOS_CLASS_DEFAULT = 0x15
QOS_CLASS_UTILITY = 0x11
QOS_CLASS_BACKGROUND = 0x09


def _set_qos_class(qos_class: int, relative_priority: int = 0) -> bool:
    """
    Set QoS class for the current thread via pthread.

    This doesn't require root — it's a scheduler hint that affects CPU priority,
    I/O priority, and timer coalescing. USER_INITIATED is appropriate for a
    monitoring daemon that needs timely wakeups.

    Args:
        qos_class: One of the QOS_CLASS_* constants
        relative_priority: -15 to 0, offset within class (0 = highest in class)

    Returns:
        True if successful, False otherwise
    """
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        pthread_set_qos = libsystem.pthread_set_qos_class_self_np
        pthread_set_qos.argtypes = [ctypes.c_uint, ctypes.c_int]
        pthread_set_qos.restype = ctypes.c_int

        result = pthread_set_qos(qos_class, relative_priority)
        return result == 0
    except (OSError, AttributeError):
        return False


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

        self.collector = LibprocCollector(config)

        # Initialize ring buffer
        max_samples = config.system.ring_buffer_size
        self.ring_buffer = RingBuffer(max_samples=max_samples)

        # Boot time for process tracking (stable across daemon restarts)
        self.boot_time = get_boot_time()

        # Database connection and tracker. Initialized in _init_database() after
        # schema validation/recreation to avoid stale connections.
        self._conn: sqlite3.Connection | None = None
        self.tracker: ProcessTracker | None = None

        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
        self._auto_prune_task: asyncio.Task | None = None
        self._socket_server: SocketServer | None = None
        self._last_forensics_time: float = 0.0  # For debouncing

    async def _forensics_callback(self, event_id: int, trigger: str) -> None:
        """Forensics callback for tracker band transitions.

        Called by ProcessTracker when a process enters high/critical band
        or escalates into one. Captures forensic data and stores in database.

        Args:
            event_id: The process event ID
            trigger: What triggered this capture (e.g., 'band_entry_high')
        """
        # Debounce: tailspin needs at least 0.5s to refill its buffer after a save
        now = time.monotonic()
        elapsed = now - self._last_forensics_time
        if elapsed < self.config.system.forensics_debounce:
            log.info(
                "forensics_debounced",
                event_id=event_id,
                trigger=trigger,
                elapsed=round(elapsed, 2),
                debounce=self.config.system.forensics_debounce,
            )
            return
        self._last_forensics_time = now

        if self._conn is None:
            log.warning("forensics_skipped_no_db", event_id=event_id, trigger=trigger)
            return

        try:
            contents = self.ring_buffer.freeze()
            capture = ForensicsCapture(self._conn, event_id, self.config.runtime_dir)
            capture_id = await capture.capture_and_store(contents, trigger)
            log.info(
                "forensics_triggered",
                event_id=event_id,
                capture_id=capture_id,
                trigger=trigger,
            )
        except Exception as e:
            log.exception(
                "forensics_callback_failed",
                event_id=event_id,
                trigger=trigger,
                error=str(e),
            )

    async def _init_database(self) -> None:
        """Initialize database connection.

        Extracted from start() so tests can initialize DB without full daemon startup.
        No migrations - if schema version mismatches, init_database() deletes and recreates.
        """
        # Create config file with defaults if it doesn't exist
        if not self.config.config_path.exists():
            self.config.save()
            log.info("config_created", path=str(self.config.config_path))

        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        db_existed = self.config.db_path.exists()
        init_database(self.config.db_path)  # Handles version check + recreate

        # Create connection and tracker AFTER init_database validates/recreates schema
        self._conn = sqlite3.connect(self.config.db_path)
        self.tracker = ProcessTracker(
            self._conn,
            self.config.bands,
            self.boot_time,
            on_forensics_trigger=self._forensics_callback,
        )

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

        log.info("daemon_starting", version=version("rogue-hunter"))

        # Log configuration for visibility
        bands = self.config.bands
        log.info(
            "daemon_config",
            ring_buffer_size=self.config.system.ring_buffer_size,
            tracking_threshold=bands.tracking_threshold,
            tracking_band=bands.tracking_band,
            band_thresholds=f"med={bands.medium}/elev={bands.elevated}/high={bands.high}/crit={bands.critical}",
        )
        log.info("daemon_boot_time", boot_time=self.boot_time)

        # Set QoS to USER_INITIATED for reliable sampling under load
        # This affects CPU scheduling, I/O priority, and timer coalescing
        # Ensures we get timely wakeups even when system is busy
        if _set_qos_class(QOS_CLASS_USER_INITIATED):
            log.info("qos_class_set", qos="USER_INITIATED")
        else:
            # Fall back to nice (requires root, will likely fail)
            try:
                os.setpriority(os.PRIO_PROCESS, 0, -10)
                log.info("daemon_priority_set", nice=-10)
            except PermissionError:
                log.info("priority_default", msg="Running at default priority")

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

        # Ensure tailspin is enabled for forensics capture
        await self._ensure_tailspin_enabled()

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

        # Run main loop (collector -> tracker -> ring buffer)
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

        # Disable tailspin tracing
        await self._disable_tailspin()

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

    async def _ensure_tailspin_enabled(self) -> None:
        """Ensure tailspin tracing is enabled for forensics capture.

        Tailspin continuously records kernel events to a rolling buffer.
        When we detect a system pause, we save this buffer to see what
        happened during the freeze. Without tailspin enabled, forensics
        captures fail with "trace too short" errors.
        """
        try:
            # Check current status
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/tailspin",
                "info",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace")

            if "has been disabled" in output:
                # Enable tailspin
                enable_proc = await asyncio.create_subprocess_exec(
                    "/usr/bin/tailspin",
                    "enable",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                await enable_proc.wait()
                log.info("tailspin_enabled", reason="was_disabled")
            else:
                log.debug("tailspin_already_enabled")

        except FileNotFoundError:
            log.warning("tailspin_not_found", msg="forensics captures will fail")
        except OSError as e:
            log.warning("tailspin_check_failed", error=str(e))

    async def _disable_tailspin(self) -> None:
        """Disable tailspin tracing on shutdown.

        We disable tailspin when shutting down to avoid leaving it running
        when the daemon isn't actively monitoring. Users who want tailspin
        enabled persistently can run `tailspin enable` manually.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/tailspin",
                "disable",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            log.info("tailspin_disabled")
        except FileNotFoundError:
            pass  # Already logged during startup
        except OSError as e:
            log.warning("tailspin_disable_failed", error=str(e))

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
        """Check if daemon is already running.

        Verifies not just that a process with the PID exists, but that it's
        actually the rogue-hunter daemon. This prevents false positives after
        a reboot when a different process may have the same PID.
        """
        if not self.config.pid_path.exists():
            return False

        try:
            pid = int(self.config.pid_path.read_text().strip())
        except ValueError:
            log.warning("pid_file_invalid", reason="not a number")
            self._remove_pid_file()
            return False

        # Check if process exists and is actually the daemon
        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()

            # Check if this is actually rogue-hunter
            cmdline_str = " ".join(cmdline).lower()
            if "rogue-hunter" in cmdline_str or "rogue_hunter" in cmdline_str:
                log.info(
                    "daemon_already_running_verified",
                    pid=pid,
                    cmdline=" ".join(cmdline[:3]),
                )
                return True
            else:
                # Process exists but it's not the daemon - stale PID file
                log.warning(
                    "pid_file_stale",
                    reason="different process",
                    pid=pid,
                    actual_process=proc.name(),
                )
                self._remove_pid_file()
                return False

        except psutil.NoSuchProcess:
            log.warning("pid_file_stale", reason="process not found", pid=pid)
            self._remove_pid_file()
            return False
        except psutil.AccessDenied:
            # Can't inspect process - assume it's running to be safe
            log.warning("pid_check_access_denied", pid=pid)
            return True

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
                    events_deleted = prune_old_data(
                        self._conn,
                        events_days=self.config.retention.events_days,
                    )
                    log.info(
                        "auto_prune_completed",
                        events_deleted=events_deleted,
                    )

    async def _main_loop(self) -> None:
        """Main loop collecting process samples at configured interval.

        Each iteration:
        1. Collect samples via LibprocCollector
        2. Enrich with low/high from ring buffer history
        3. Push enriched sample to ring buffer
        4. Update per-process tracking (triggers forensics on band entry)
        5. Broadcast to TUI via socket
        6. Sleep for remaining interval to maintain sample rate

        Sample rate is controlled by config.system.sample_interval (default 0.2s = 5Hz).
        The loop runs until shutdown event is set.
        """
        # Heartbeat tracking (log every 60 samples = ~1 minute)
        heartbeat_interval = 60
        heartbeat_count = 0
        heartbeat_max_score = 0
        heartbeat_score_sum = 0

        # Track rogue selection churn (PIDs entering/leaving selection)
        previous_rogues: dict[int, str] = {}  # pid -> command
        logged_rogues: set[int] = set()  # PIDs we logged as "entered" (above threshold)
        log_threshold = 10  # Only log enter/exit for scores above this

        sample_interval = self.config.system.sample_interval

        while not self._shutdown_event.is_set():
            try:
                iteration_start = asyncio.get_event_loop().time()

                # Collect samples
                samples = await self.collector.collect()

                if self._shutdown_event.is_set():
                    break

                # Log rogue selection churn (new/exited processes)
                current_rogues = {r.pid: r.command for r in samples.rogues}
                current_pids = set(current_rogues.keys())
                previous_pids = set(previous_rogues.keys())

                # New processes entering rogue selection (only log if above threshold)
                for pid in current_pids - previous_pids:
                    rogue = next(r for r in samples.rogues if r.pid == pid)
                    if rogue.score.current > log_threshold:
                        log.info(
                            "rogue_entered",
                            command=rogue.command,
                            score=rogue.score.current,
                            pid=pid,
                            dominant_category=rogue.dominant_category,
                            dominant_metrics=rogue.dominant_metrics,
                        )
                        logged_rogues.add(pid)

                # Processes exiting rogue selection (only log if we logged their entry)
                for pid in previous_pids - current_pids:
                    if pid in logged_rogues:
                        log.info(
                            "rogue_exited",
                            command=previous_rogues[pid],
                            pid=pid,
                        )
                        logged_rogues.discard(pid)

                previous_rogues = current_rogues

                # Enrich with low/high from existing ring buffer history
                samples = self._compute_pid_low_high(samples)

                # Push enriched sample to ring buffer
                self.ring_buffer.push(samples)

                # Update per-process tracking with enriched data
                # (tracker persists to storage, needs full MetricValue)
                if self.tracker is not None:
                    self.tracker.update(samples.rogues)

                # Update heartbeat stats
                heartbeat_count += 1
                heartbeat_score_sum += samples.max_score
                heartbeat_max_score = max(heartbeat_max_score, samples.max_score)

                # Broadcast to TUI clients (with enriched low/high data)
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

                # Sleep for remaining interval (maintains consistent sample rate)
                elapsed = asyncio.get_event_loop().time() - iteration_start
                sleep_time = sample_interval - elapsed
                if sleep_time > 0:
                    try:
                        await asyncio.wait_for(self._shutdown_event.wait(), timeout=sleep_time)
                        break  # Shutdown requested during sleep
                    except asyncio.TimeoutError:
                        pass  # Normal timeout, continue to next sample

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

    def _get_pid_history(self, pid: int) -> list[ProcessScore]:
        """Get all ProcessScore entries for a PID from ring buffer."""
        history = []
        for ring_sample in self.ring_buffer.samples:
            for rogue in ring_sample.samples.rogues:
                if rogue.pid == pid:
                    history.append(rogue)
        return history

    def _enrich_metric(
        self, current: MetricValue, history_values: list[float | int]
    ) -> MetricValue:
        """Compute low/high from history and create enriched MetricValue."""
        if not history_values:
            return current
        all_values = history_values + [current.current]
        return MetricValue(
            current=current.current,
            low=min(all_values),
            high=max(all_values),
        )

    def _enrich_metric_str(
        self, current: MetricValueStr, history_values: list[str], severity_map: dict[str, int]
    ) -> MetricValueStr:
        """Compute low/high from history using severity ordering."""
        if not history_values:
            return current
        all_values = history_values + [current.current]
        severities = [(v, severity_map.get(v, 0)) for v in all_values]
        sorted_by_severity = sorted(severities, key=lambda x: x[1])
        return MetricValueStr(
            current=current.current,
            low=sorted_by_severity[0][0],  # least severe
            high=sorted_by_severity[-1][0],  # most severe
        )

    def _enrich_with_low_high(
        self, rogue: ProcessScore, history: list[ProcessScore]
    ) -> ProcessScore:
        """Enrich a ProcessScore with low/high computed from history."""

        # Extract history values for each field
        def hist_vals(attr: str) -> list[float | int]:
            return [getattr(h, attr).current for h in history]

        def hist_vals_str(attr: str) -> list[str]:
            return [getattr(h, attr).current for h in history]

        return ProcessScore(
            pid=rogue.pid,
            command=rogue.command,
            captured_at=rogue.captured_at,
            # CPU
            cpu=self._enrich_metric(rogue.cpu, hist_vals("cpu")),
            # Memory
            mem=self._enrich_metric(rogue.mem, hist_vals("mem")),
            mem_peak=rogue.mem_peak,  # Lifetime peak, no range needed
            pageins=self._enrich_metric(rogue.pageins, hist_vals("pageins")),
            pageins_rate=self._enrich_metric(rogue.pageins_rate, hist_vals("pageins_rate")),
            faults=self._enrich_metric(rogue.faults, hist_vals("faults")),
            faults_rate=self._enrich_metric(rogue.faults_rate, hist_vals("faults_rate")),
            # Disk I/O
            disk_io=self._enrich_metric(rogue.disk_io, hist_vals("disk_io")),
            disk_io_rate=self._enrich_metric(rogue.disk_io_rate, hist_vals("disk_io_rate")),
            # Activity
            csw=self._enrich_metric(rogue.csw, hist_vals("csw")),
            csw_rate=self._enrich_metric(rogue.csw_rate, hist_vals("csw_rate")),
            syscalls=self._enrich_metric(rogue.syscalls, hist_vals("syscalls")),
            syscalls_rate=self._enrich_metric(rogue.syscalls_rate, hist_vals("syscalls_rate")),
            threads=self._enrich_metric(rogue.threads, hist_vals("threads")),
            mach_msgs=self._enrich_metric(rogue.mach_msgs, hist_vals("mach_msgs")),
            mach_msgs_rate=self._enrich_metric(rogue.mach_msgs_rate, hist_vals("mach_msgs_rate")),
            # Efficiency
            instructions=self._enrich_metric(rogue.instructions, hist_vals("instructions")),
            cycles=self._enrich_metric(rogue.cycles, hist_vals("cycles")),
            ipc=self._enrich_metric(rogue.ipc, hist_vals("ipc")),
            # Power
            energy=self._enrich_metric(rogue.energy, hist_vals("energy")),
            energy_rate=self._enrich_metric(rogue.energy_rate, hist_vals("energy_rate")),
            wakeups=self._enrich_metric(rogue.wakeups, hist_vals("wakeups")),
            wakeups_rate=self._enrich_metric(rogue.wakeups_rate, hist_vals("wakeups_rate")),
            # Contention
            runnable_time=self._enrich_metric(rogue.runnable_time, hist_vals("runnable_time")),
            runnable_time_rate=self._enrich_metric(
                rogue.runnable_time_rate, hist_vals("runnable_time_rate")
            ),
            qos_interactive=self._enrich_metric(
                rogue.qos_interactive, hist_vals("qos_interactive")
            ),
            qos_interactive_rate=self._enrich_metric(
                rogue.qos_interactive_rate, hist_vals("qos_interactive_rate")
            ),
            # GPU
            gpu_time=self._enrich_metric(rogue.gpu_time, hist_vals("gpu_time")),
            gpu_time_rate=self._enrich_metric(rogue.gpu_time_rate, hist_vals("gpu_time_rate")),
            # Zombie children
            zombie_children=self._enrich_metric(
                rogue.zombie_children, hist_vals("zombie_children")
            ),
            # State (categorical)
            state=self._enrich_metric_str(rogue.state, hist_vals_str("state"), STATE_SEVERITY),
            priority=self._enrich_metric(rogue.priority, hist_vals("priority")),
            # Scoring (4-category system)
            score=self._enrich_metric(rogue.score, hist_vals("score")),
            band=self._enrich_metric_str(rogue.band, hist_vals_str("band"), BAND_SEVERITY),
            blocking_score=self._enrich_metric(rogue.blocking_score, hist_vals("blocking_score")),
            contention_score=self._enrich_metric(
                rogue.contention_score, hist_vals("contention_score")
            ),
            pressure_score=self._enrich_metric(rogue.pressure_score, hist_vals("pressure_score")),
            efficiency_score=self._enrich_metric(
                rogue.efficiency_score, hist_vals("efficiency_score")
            ),
            dominant_category=rogue.dominant_category,
            dominant_metrics=rogue.dominant_metrics,
        )

    def _compute_pid_low_high(self, samples: ProcessSamples) -> ProcessSamples:
        """Enrich ProcessScore with low/high from ring buffer history."""
        enriched_rogues = []

        for rogue in samples.rogues:
            # Collect history for this PID from ring buffer
            history = self._get_pid_history(rogue.pid)
            # Enrich with low/high values
            enriched = self._enrich_with_low_high(rogue, history)
            enriched_rogues.append(enriched)

        return ProcessSamples(
            timestamp=samples.timestamp,
            elapsed_ms=samples.elapsed_ms,
            process_count=samples.process_count,
            max_score=samples.max_score,
            rogues=enriched_rogues,
        )


def _add_source(source: str) -> structlog.types.Processor:
    """Create a processor that adds a source field to log events."""

    def processor(
        logger: structlog.types.WrappedLogger,
        method_name: str,
        event_dict: structlog.types.EventDict,
    ) -> structlog.types.EventDict:
        event_dict["source"] = source
        return event_dict

    return processor


def _setup_logging(config: "Config") -> None:
    """Configure structlog with dual output: console + JSON file.

    Console output uses human-readable format with colors.
    File output uses JSON Lines format for machine parsing.
    Both use local time to match sample timestamps.
    """
    # Ensure state directory exists for log file
    config.state_dir.mkdir(parents=True, exist_ok=True)

    # Set up rotating file handler for JSON output
    # 5MB max size, keep 3 backup files
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_path,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    # Configure stdlib logging for file output
    # structlog will use this for JSON output via ProcessorFormatter
    stdlib_root = logging.getLogger()
    stdlib_root.setLevel(logging.INFO)

    # Clear any existing handlers
    stdlib_root.handlers.clear()

    # Add file handler with JSON formatter
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso", utc=False, key="ts"),
                structlog.processors.add_log_level,
                _add_source("daemon"),
                structlog.processors.format_exc_info,
            ],
        )
    )
    stdlib_root.addHandler(file_handler)

    # Configure structlog with console output (primary) and stdlib passthrough (for file)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
            structlog.processors.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also add a console handler for human-readable output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(),
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
            ],
        )
    )
    stdlib_root.addHandler(console_handler)


async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon until shutdown.

    Args:
        config: Optional config, loads from file if not provided
    """
    if config is None:
        config = Config.load()

    # Setup dual logging: console (human-readable) + file (JSON Lines)
    _setup_logging(config)

    daemon = Daemon(config)

    try:
        await daemon.start()
    except Exception as e:
        log.exception("daemon_crashed", error=str(e))
        raise
    finally:
        await daemon.stop()
