"""Background daemon for rogue-hunter."""

import asyncio
import ctypes
import os
import resource
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import psutil

from rogue_hunter import logging as rlog
from rogue_hunter.boottime import get_boot_time
from rogue_hunter.collector import (
    BAND_SEVERITY,
    LibprocCollector,
)
from rogue_hunter.config import Config
from rogue_hunter.forensics import ForensicsCapture
from rogue_hunter.ringbuffer import RingBuffer
from rogue_hunter.socket_server import SocketServer
from rogue_hunter.storage import init_database, prune_old_data
from rogue_hunter.tracker import ProcessTracker

log = rlog.get_structlog()


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
            cooldown = self.config.system.forensics_debounce
            rlog.forensics_debounced(elapsed, cooldown)
            return
        self._last_forensics_time = now

        if self._conn is None:
            rlog.forensics_skipped("no database")
            return

        try:
            contents = self.ring_buffer.freeze()
            capture = ForensicsCapture(
                self._conn,
                event_id,
                self.config.runtime_dir,
                log_seconds=self.config.system.forensics_log_seconds,
            )
            capture_id = await capture.capture_and_store(contents, trigger)
            rlog.forensics_captured(event_id, capture_id)
        except Exception:
            log.exception(
                "forensics_callback_failed",
                event_id=event_id,
                trigger=trigger,
            )

    async def _init_database(self) -> None:
        """Initialize database connection.

        Extracted from start() so tests can initialize DB without full daemon startup.
        No migrations - if schema version mismatches, init_database() deletes and recreates.
        """
        # Create config file with defaults if it doesn't exist
        if not self.config.config_path.exists():
            self.config.save()
            rlog.config_created(str(self.config.config_path))

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
        status = "restored" if db_existed else "initialized"
        rlog.database_status(status, restored_count)

    async def start(self) -> None:
        """Start the daemon."""
        from importlib.metadata import version

        ver = version("rogue-hunter")
        rlog.version_info("rogue-hunter", ver)

        # Log configuration for visibility
        bands = self.config.bands
        buf = self.config.system.ring_buffer_size
        rlog.config_summary(buf, bands.tracking_threshold)
        rlog.bands_summary(bands.medium, bands.elevated, bands.high, bands.critical)

        # Set QoS to USER_INITIATED for reliable sampling under load
        # This affects CPU scheduling, I/O priority, and timer coalescing
        # Ensures we get timely wakeups even when system is busy
        if _set_qos_class(QOS_CLASS_USER_INITIATED):
            rlog.qos_set("USER_INITIATED")
        else:
            # Fall back to nice (requires root, will likely fail)
            try:
                os.setpriority(os.PRIO_PROCESS, 0, -10)
                rlog.priority_set("nice -10")
            except PermissionError:
                rlog.priority_default()

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        # Check for existing instance
        if self._check_already_running():
            rlog.already_running()
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
        rlog.daemon_started()

        # Start auto-prune task
        self._auto_prune_task = asyncio.create_task(self._auto_prune())

        # Run main loop (collector -> tracker -> ring buffer)
        await self._main_loop()

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        rlog.daemon_stopping()
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

        rlog.daemon_stopped()

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signals."""
        rlog.signal_received(sig.name)
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
            pass  # Silent success
        except FileNotFoundError:
            rlog.caffeinate_not_found()

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
            log.debug("Caffeinate stopped")

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
                rlog.tailspin_enabled()
            else:
                pass  # Silent - already enabled

        except FileNotFoundError:
            rlog.tailspin_not_found()
        except OSError as e:
            rlog.tailspin_check_failed(str(e))

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
            rlog.tailspin_disabled()
        except FileNotFoundError:
            pass  # Already logged during startup
        except OSError as e:
            rlog.tailspin_disable_failed(str(e))

    def _write_pid_file(self) -> None:
        """Write PID file."""
        self.config.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.pid_path.write_text(str(os.getpid()))

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        if self.config.pid_path.exists():
            self.config.pid_path.unlink()

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
            rlog.pid_file_invalid()
            self._remove_pid_file()
            return False

        # Check if process exists and is actually the daemon
        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()

            # Check if this is actually rogue-hunter
            cmdline_str = " ".join(cmdline).lower()
            if "rogue-hunter" in cmdline_str or "rogue_hunter" in cmdline_str:
                rlog.daemon_already_running(pid)
                return True
            else:
                # Process exists but it's not the daemon - stale PID file
                rlog.stale_pid_file(pid, proc.name())
                self._remove_pid_file()
                return False

        except psutil.NoSuchProcess:
            rlog.stale_pid_not_found(pid)
            self._remove_pid_file()
            return False
        except psutil.AccessDenied:
            # Can't inspect process - assume it's running to be safe
            rlog.pid_verify_failed(pid)
            return True

    async def _auto_prune(self) -> None:
        """Run automatic data pruning periodically."""
        prune_interval_seconds = self.config.system.auto_prune_interval_hours * 3600
        while not self._shutdown_event.is_set():
            try:
                # Wait for configured interval or shutdown
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=prune_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                # Run prune
                if self._conn:
                    rlog.auto_prune_started()
                    events_deleted = prune_old_data(
                        self._conn,
                        events_days=self.config.retention.events_days,
                    )
                    rlog.auto_prune_complete(events_deleted)

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
        # Heartbeat tracking (log every N samples, configurable)
        heartbeat_interval = self.config.system.heartbeat_samples
        heartbeat_count = 0
        heartbeat_max_score = 0
        heartbeat_score_sum = 0

        # Track band transitions for logging with stability filter
        # Key: pid, Value: (logged_band, current_band, command, consecutive, last_seen)
        # - logged_band: the band we last logged for this process
        # - current_band: the band the process is currently at
        # - consecutive: samples at current_band (for stability filter)
        # - last_seen: sample count when last seen (for staleness pruning)
        # We log entry only after N consecutive samples at new higher band
        tracked_bands: dict[int, tuple[str, str, str, int, int]] = {}
        sample_count = 0
        stability_threshold = self.config.system.log_stability_samples
        stale_threshold = 1500  # Prune entries not seen in ~5 minutes

        sample_interval = self.config.system.sample_interval

        while not self._shutdown_event.is_set():
            try:
                iteration_start = asyncio.get_event_loop().time()

                # Collect samples
                samples = await self.collector.collect()

                if self._shutdown_event.is_set():
                    break

                # Build current state from rogues
                current_rogues = {r.pid: r for r in samples.rogues}

                sample_count += 1

                # Check for band transitions with stability filter
                for pid, rogue in current_rogues.items():
                    band = rogue.band
                    # Get previous state: (logged_band, current_band, cmd, consecutive, last_seen)
                    logged_band, prev_band, _, consecutive, _ = tracked_bands.get(
                        pid, ("low", "low", "", 0, 0)
                    )

                    # Update consecutive counter
                    if band == prev_band:
                        consecutive += 1
                    else:
                        consecutive = 1  # Band changed, reset counter

                    # Check for escalation: band is higher than what we logged AND stable
                    if (
                        BAND_SEVERITY.get(band, 0) > BAND_SEVERITY.get(logged_band, 0)
                        and band in ("medium", "elevated", "high", "critical")
                        and consecutive >= stability_threshold
                    ):
                        metrics = f"{rogue.dominant_resource}: {rogue.disproportionality:.1f}x"
                        rlog.rogue_enter(rogue.command, pid, rogue.score, metrics)
                        logged_band = band  # Update logged band

                    # Check for exit: dropped to "low" AND stable AND we had logged something
                    if (
                        band == "low"
                        and logged_band != "low"
                        and consecutive >= stability_threshold
                    ):
                        rlog.rogue_exit(rogue.command, pid)
                        logged_band = "low"

                    # Update tracked state
                    tracked_bands[pid] = (
                        logged_band,
                        band,
                        rogue.command,
                        consecutive,
                        sample_count,
                    )

                # Prune stale entries (not seen in stale_threshold samples)
                # This prevents memory growth while keeping state for processes
                # that temporarily leave the top-N rogue selection
                tracked_bands = {
                    pid: v
                    for pid, v in tracked_bands.items()
                    if sample_count - v[4] < stale_threshold
                }

                # Push sample to ring buffer
                self.ring_buffer.push(samples)

                # Update per-process tracking
                if self.tracker is not None:
                    self.tracker.update(samples.rogues)

                # Update heartbeat stats
                heartbeat_count += 1
                heartbeat_score_sum += samples.max_score
                heartbeat_max_score = max(heartbeat_max_score, samples.max_score)

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

                    avg_score = round(heartbeat_score_sum / heartbeat_count)
                    rlog.heartbeat(
                        avg_score=avg_score,
                        max_score=heartbeat_max_score,
                        tracked_count=tracked_count,
                        buffer_size=buffer_size,
                        buffer_capacity=self.ring_buffer.capacity,
                        client_count=client_count,
                        rss_mb=rss_mb,
                        db_size_mb=db_size_mb,
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
                rlog.main_loop_cancelled()
                break
            except Exception as e:
                log.warning("sample_failed", exc_info=True)
                rlog.sample_failed(str(e))
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

    # Setup dual logging: console (human-readable) + file (JSON Lines)
    rlog.configure(config)

    daemon = Daemon(config)

    try:
        await daemon.start()
    except Exception:
        log.exception("daemon_crashed")
        raise
    finally:
        await daemon.stop()
