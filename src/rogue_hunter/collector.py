"""Process data collector using macOS top command."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

from rogue_hunter.config import Config

log = structlog.get_logger()

# Severity orderings for categorical metrics
STATE_SEVERITY = {
    "idle": 0,
    "sleeping": 1,
    "running": 2,
    "stopped": 3,
    "halted": 4,
    "zombie": 5,
    "stuck": 6,
}
BAND_SEVERITY = {
    "low": 0,
    "medium": 1,
    "elevated": 2,
    "high": 3,
    "critical": 4,
}


@dataclass
class MetricValue:
    """A metric with current value and buffer-window range."""

    current: float | int
    low: float | int
    high: float | int

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {"current": self.current, "low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, d: dict) -> "MetricValue":
        """Deserialize from a dictionary."""
        return cls(current=d["current"], low=d["low"], high=d["high"])


@dataclass
class MetricValueStr:
    """A categorical metric with hierarchy (for state/band)."""

    current: str
    low: str  # best (least concerning)
    high: str  # worst (most concerning)

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {"current": self.current, "low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, d: dict) -> "MetricValueStr":
        """Deserialize from a dictionary."""
        return cls(current=d["current"], low=d["low"], high=d["high"])


@dataclass
class ProcessScore:
    """Single process with metrics and buffer-window ranges.

    This is THE canonical data schema for process data.
    DO NOT create alternative representations.
    """

    # ─────────────────────────────────────────────────────────────
    # Identity (no range — these don't vary)
    # ─────────────────────────────────────────────────────────────
    pid: int
    command: str
    captured_at: float

    # ─────────────────────────────────────────────────────────────
    # CPU
    # ─────────────────────────────────────────────────────────────
    cpu: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Memory
    # ─────────────────────────────────────────────────────────────
    mem: MetricValue
    mem_peak: int  # Lifetime peak (doesn't need range)
    pageins: MetricValue  # Cumulative (for reference)
    pageins_rate: MetricValue  # Page-ins per second
    faults: MetricValue  # Cumulative (for reference)
    faults_rate: MetricValue  # Faults per second

    # ─────────────────────────────────────────────────────────────
    # Disk I/O
    # ─────────────────────────────────────────────────────────────
    disk_io: MetricValue
    disk_io_rate: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Activity
    # ─────────────────────────────────────────────────────────────
    csw: MetricValue  # Cumulative (for reference)
    csw_rate: MetricValue  # Context switches per second
    syscalls: MetricValue  # Cumulative (for reference)
    syscalls_rate: MetricValue  # Syscalls per second
    threads: MetricValue
    mach_msgs: MetricValue  # Cumulative (for reference)
    mach_msgs_rate: MetricValue  # Messages per second

    # ─────────────────────────────────────────────────────────────
    # Efficiency
    # ─────────────────────────────────────────────────────────────
    instructions: MetricValue
    cycles: MetricValue
    ipc: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Power
    # ─────────────────────────────────────────────────────────────
    energy: MetricValue
    energy_rate: MetricValue
    wakeups: MetricValue  # Cumulative (for reference)
    wakeups_rate: MetricValue  # Wakeups per second

    # ─────────────────────────────────────────────────────────────
    # Contention (scheduler pressure indicators)
    # ─────────────────────────────────────────────────────────────
    runnable_time: MetricValue  # Cumulative runnable time (ns)
    runnable_time_rate: MetricValue  # ms runnable per second
    qos_interactive: MetricValue  # Cumulative QoS interactive time (ns)
    qos_interactive_rate: MetricValue  # ms interactive per second

    # ─────────────────────────────────────────────────────────────
    # State (categorical with hierarchy)
    # ─────────────────────────────────────────────────────────────
    state: MetricValueStr
    priority: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Scoring (4-category system)
    # ─────────────────────────────────────────────────────────────
    score: MetricValue  # Final weighted score 0-100
    band: MetricValueStr  # low/medium/elevated/high/critical
    blocking_score: MetricValue  # 0-100, causes pauses
    contention_score: MetricValue  # 0-100, fighting for resources
    pressure_score: MetricValue  # 0-100, stressing system
    efficiency_score: MetricValue  # 0-100, wasting resources
    dominant_category: str  # "blocking"|"contention"|"pressure"|"efficiency"
    dominant_metrics: list[str]  # ["pageins:847/s", "disk:42M/s"]

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {
            "pid": self.pid,
            "command": self.command,
            "captured_at": self.captured_at,
            # CPU
            "cpu": self.cpu.to_dict(),
            # Memory
            "mem": self.mem.to_dict(),
            "mem_peak": self.mem_peak,
            "pageins": self.pageins.to_dict(),
            "pageins_rate": self.pageins_rate.to_dict(),
            "faults": self.faults.to_dict(),
            "faults_rate": self.faults_rate.to_dict(),
            # Disk I/O
            "disk_io": self.disk_io.to_dict(),
            "disk_io_rate": self.disk_io_rate.to_dict(),
            # Activity
            "csw": self.csw.to_dict(),
            "csw_rate": self.csw_rate.to_dict(),
            "syscalls": self.syscalls.to_dict(),
            "syscalls_rate": self.syscalls_rate.to_dict(),
            "threads": self.threads.to_dict(),
            "mach_msgs": self.mach_msgs.to_dict(),
            "mach_msgs_rate": self.mach_msgs_rate.to_dict(),
            # Efficiency
            "instructions": self.instructions.to_dict(),
            "cycles": self.cycles.to_dict(),
            "ipc": self.ipc.to_dict(),
            # Power
            "energy": self.energy.to_dict(),
            "energy_rate": self.energy_rate.to_dict(),
            "wakeups": self.wakeups.to_dict(),
            "wakeups_rate": self.wakeups_rate.to_dict(),
            # Contention
            "runnable_time": self.runnable_time.to_dict(),
            "runnable_time_rate": self.runnable_time_rate.to_dict(),
            "qos_interactive": self.qos_interactive.to_dict(),
            "qos_interactive_rate": self.qos_interactive_rate.to_dict(),
            # State
            "state": self.state.to_dict(),
            "priority": self.priority.to_dict(),
            # Scoring
            "score": self.score.to_dict(),
            "band": self.band.to_dict(),
            "blocking_score": self.blocking_score.to_dict(),
            "contention_score": self.contention_score.to_dict(),
            "pressure_score": self.pressure_score.to_dict(),
            "efficiency_score": self.efficiency_score.to_dict(),
            "dominant_category": self.dominant_category,
            "dominant_metrics": self.dominant_metrics,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessScore":
        """Deserialize from a dictionary."""
        return cls(
            pid=data["pid"],
            command=data["command"],
            captured_at=data["captured_at"],
            # CPU
            cpu=MetricValue.from_dict(data["cpu"]),
            # Memory
            mem=MetricValue.from_dict(data["mem"]),
            mem_peak=data["mem_peak"],
            pageins=MetricValue.from_dict(data["pageins"]),
            pageins_rate=MetricValue.from_dict(data["pageins_rate"]),
            faults=MetricValue.from_dict(data["faults"]),
            faults_rate=MetricValue.from_dict(data["faults_rate"]),
            # Disk I/O
            disk_io=MetricValue.from_dict(data["disk_io"]),
            disk_io_rate=MetricValue.from_dict(data["disk_io_rate"]),
            # Activity
            csw=MetricValue.from_dict(data["csw"]),
            csw_rate=MetricValue.from_dict(data["csw_rate"]),
            syscalls=MetricValue.from_dict(data["syscalls"]),
            syscalls_rate=MetricValue.from_dict(data["syscalls_rate"]),
            threads=MetricValue.from_dict(data["threads"]),
            mach_msgs=MetricValue.from_dict(data["mach_msgs"]),
            mach_msgs_rate=MetricValue.from_dict(data["mach_msgs_rate"]),
            # Efficiency
            instructions=MetricValue.from_dict(data["instructions"]),
            cycles=MetricValue.from_dict(data["cycles"]),
            ipc=MetricValue.from_dict(data["ipc"]),
            # Power
            energy=MetricValue.from_dict(data["energy"]),
            energy_rate=MetricValue.from_dict(data["energy_rate"]),
            wakeups=MetricValue.from_dict(data["wakeups"]),
            wakeups_rate=MetricValue.from_dict(data["wakeups_rate"]),
            # Contention
            runnable_time=MetricValue.from_dict(data["runnable_time"]),
            runnable_time_rate=MetricValue.from_dict(data["runnable_time_rate"]),
            qos_interactive=MetricValue.from_dict(data["qos_interactive"]),
            qos_interactive_rate=MetricValue.from_dict(data["qos_interactive_rate"]),
            # State
            state=MetricValueStr.from_dict(data["state"]),
            priority=MetricValue.from_dict(data["priority"]),
            # Scoring
            score=MetricValue.from_dict(data["score"]),
            band=MetricValueStr.from_dict(data["band"]),
            blocking_score=MetricValue.from_dict(data["blocking_score"]),
            contention_score=MetricValue.from_dict(data["contention_score"]),
            pressure_score=MetricValue.from_dict(data["pressure_score"]),
            efficiency_score=MetricValue.from_dict(data["efficiency_score"]),
            dominant_category=data["dominant_category"],
            dominant_metrics=data["dominant_metrics"],
        )

    @classmethod
    def from_storage(cls, data: dict, pid: int, command: str) -> "ProcessScore":
        """Reconstruct ProcessScore from storage dict format.

        Storage dicts have MetricValue fields as {"current": x, "low": y, "high": z}.
        This is the inverse of how storage.get_snapshot() returns data.

        Args:
            data: Storage dict with MetricValue-compatible fields
            pid: Process ID (not stored in snapshot)
            command: Process command (not stored in snapshot)
        """
        return cls(
            pid=pid,
            command=command,
            captured_at=data["captured_at"],
            # CPU
            cpu=MetricValue.from_dict(data["cpu"]),
            # Memory
            mem=MetricValue.from_dict(data["mem"]),
            mem_peak=data["mem_peak"],
            pageins=MetricValue.from_dict(data["pageins"]),
            pageins_rate=MetricValue.from_dict(data["pageins_rate"]),
            faults=MetricValue.from_dict(data["faults"]),
            faults_rate=MetricValue.from_dict(data["faults_rate"]),
            # Disk I/O
            disk_io=MetricValue.from_dict(data["disk_io"]),
            disk_io_rate=MetricValue.from_dict(data["disk_io_rate"]),
            # Activity
            csw=MetricValue.from_dict(data["csw"]),
            csw_rate=MetricValue.from_dict(data["csw_rate"]),
            syscalls=MetricValue.from_dict(data["syscalls"]),
            syscalls_rate=MetricValue.from_dict(data["syscalls_rate"]),
            threads=MetricValue.from_dict(data["threads"]),
            mach_msgs=MetricValue.from_dict(data["mach_msgs"]),
            mach_msgs_rate=MetricValue.from_dict(data["mach_msgs_rate"]),
            # Efficiency
            instructions=MetricValue.from_dict(data["instructions"]),
            cycles=MetricValue.from_dict(data["cycles"]),
            ipc=MetricValue.from_dict(data["ipc"]),
            # Power
            energy=MetricValue.from_dict(data["energy"]),
            energy_rate=MetricValue.from_dict(data["energy_rate"]),
            wakeups=MetricValue.from_dict(data["wakeups"]),
            wakeups_rate=MetricValue.from_dict(data["wakeups_rate"]),
            # Contention
            runnable_time=MetricValue.from_dict(data["runnable_time"]),
            runnable_time_rate=MetricValue.from_dict(data["runnable_time_rate"]),
            qos_interactive=MetricValue.from_dict(data["qos_interactive"]),
            qos_interactive_rate=MetricValue.from_dict(data["qos_interactive_rate"]),
            # State
            state=MetricValueStr.from_dict(data["state"]),
            priority=MetricValue.from_dict(data["priority"]),
            # Scoring
            score=MetricValue.from_dict(data["score"]),
            band=MetricValueStr.from_dict(data["band"]),
            blocking_score=MetricValue.from_dict(data["blocking_score"]),
            contention_score=MetricValue.from_dict(data["contention_score"]),
            pressure_score=MetricValue.from_dict(data["pressure_score"]),
            efficiency_score=MetricValue.from_dict(data["efficiency_score"]),
            dominant_category=data["dominant_category"],
            dominant_metrics=data["dominant_metrics"],
        )


@dataclass
class ProcessSamples:
    """Collection of scored processes from one sample."""

    timestamp: datetime
    elapsed_ms: int
    process_count: int
    max_score: int
    rogues: list[ProcessScore]

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "elapsed_ms": self.elapsed_ms,
                "process_count": self.process_count,
                "max_score": self.max_score,
                "rogues": [r.to_dict() for r in self.rogues],
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "ProcessSamples":
        """Deserialize from JSON string."""
        d = json.loads(data)
        return cls(
            timestamp=datetime.fromisoformat(d["timestamp"]),
            elapsed_ms=d["elapsed_ms"],
            process_count=d["process_count"],
            max_score=d["max_score"],
            rogues=[ProcessScore.from_dict(r) for r in d["rogues"]],
        )


def get_core_count() -> int:
    """Get number of CPU cores."""
    return os.cpu_count() or 1


@dataclass
class _PrevSample:
    """Previous sample state for delta calculations.

    All cumulative metrics that need rate calculations are stored here.
    """

    cpu_time_ns: int  # Total CPU time (user + system) in nanoseconds
    disk_io: int  # Total disk I/O bytes (read + write)
    energy: int  # Total energy billed
    timestamp: float  # time.monotonic() when sampled
    # Cumulative values that need rate calculation
    pageins: int  # Total page-ins
    csw: int  # Total context switches
    syscalls: int  # Total syscalls (mach + unix)
    mach_msgs: int  # Total mach messages (sent + received)
    wakeups: int  # Total wakeups (pkg_idle + interrupt)
    faults: int  # Total page faults
    runnable_time: int  # Total runnable time (mach time units)
    qos_interactive: int  # Total QoS interactive time (mach time units)


class LibprocCollector:
    """Collects process data via direct libproc.dylib calls.

    Uses native macOS APIs (proc_pid_rusage, proc_pidinfo) for efficient
    process monitoring. Maintains internal state for CPU% delta calculations.

    Performance: ~10-50ms per collection.
    """

    def __init__(self, config: Config):
        self.config = config
        self._prev_samples: dict[int, _PrevSample] = {}  # pid -> previous sample
        self._last_collect_time: float = 0.0

        # Get timebase info once (for Apple Silicon time conversion)
        from rogue_hunter.libproc import get_timebase_info

        self._timebase = get_timebase_info()

    def _collect_sync(self) -> ProcessSamples:
        """Synchronous collection - runs in executor."""
        from rogue_hunter.libproc import (
            abs_to_ns,
            get_bsd_info,
            get_process_name,
            get_rusage,
            get_state_name,
            get_task_info,
            list_all_pids,
        )

        start = time.monotonic()

        # Time delta since last collection
        if self._last_collect_time > 0:
            wall_delta_ns = (start - self._last_collect_time) * 1e9
        else:
            wall_delta_ns = 0.0
        self._last_collect_time = start

        # Collect all PIDs
        pids = list_all_pids()
        all_processes: list[dict] = []
        current_pids: set[int] = set()

        for pid in pids:
            # Skip kernel PID 0
            if pid == 0:
                continue

            # Get rusage (richest single call)
            rusage = get_rusage(pid)
            if rusage is None:
                continue  # Process disappeared or permission denied

            # Get task info for context switches, syscalls, threads
            task_info = get_task_info(pid)
            if task_info is None:
                continue

            # Get BSD info for state
            bsd_info = get_bsd_info(pid)
            if bsd_info is None:
                continue

            # Track this PID
            current_pids.add(pid)

            # Convert CPU times from mach_absolute_time to nanoseconds
            user_ns = abs_to_ns(rusage.ri_user_time, self._timebase)
            system_ns = abs_to_ns(rusage.ri_system_time, self._timebase)
            total_cpu_ns = user_ns + system_ns

            # Extract cumulative values for rate calculation
            disk_io = rusage.ri_diskio_bytesread + rusage.ri_diskio_byteswritten
            energy = rusage.ri_billed_energy
            instructions = rusage.ri_instructions
            cycles = rusage.ri_cycles

            # Extract cumulative values that were previously scored incorrectly
            pageins = rusage.ri_pageins
            csw = task_info.pti_csw
            syscalls = task_info.pti_syscalls_mach + task_info.pti_syscalls_unix
            mach_msgs = task_info.pti_messages_sent + task_info.pti_messages_received
            wakeups = rusage.ri_pkg_idle_wkups + rusage.ri_interrupt_wkups
            faults = task_info.pti_faults
            runnable_time = rusage.ri_runnable_time
            qos_interactive = rusage.ri_cpu_time_qos_user_interactive

            # Calculate deltas/rates from previous sample
            cpu_percent = 0.0
            disk_io_rate = 0.0
            energy_rate = 0.0
            pageins_rate = 0.0
            csw_rate = 0.0
            syscalls_rate = 0.0
            mach_msgs_rate = 0.0
            wakeups_rate = 0.0
            faults_rate = 0.0
            runnable_time_rate = 0.0  # ms of runnable per second
            qos_interactive_rate = 0.0  # ms of interactive QoS per second

            wall_delta_sec = wall_delta_ns / 1e9

            if wall_delta_ns > 0 and pid in self._prev_samples:
                prev = self._prev_samples[pid]
                # CPU%
                cpu_delta_ns = total_cpu_ns - prev.cpu_time_ns
                if cpu_delta_ns > 0:
                    cpu_percent = (cpu_delta_ns / wall_delta_ns) * 100.0
                # Disk I/O rate (bytes/sec)
                disk_delta = disk_io - prev.disk_io
                if disk_delta > 0 and wall_delta_sec > 0:
                    disk_io_rate = disk_delta / wall_delta_sec
                # Energy rate (energy units/sec)
                energy_delta = energy - prev.energy
                if energy_delta > 0 and wall_delta_sec > 0:
                    energy_rate = energy_delta / wall_delta_sec

                # New rate calculations
                if wall_delta_sec > 0:
                    pageins_delta = pageins - prev.pageins
                    if pageins_delta > 0:
                        pageins_rate = pageins_delta / wall_delta_sec

                    csw_delta = csw - prev.csw
                    if csw_delta > 0:
                        csw_rate = csw_delta / wall_delta_sec

                    syscalls_delta = syscalls - prev.syscalls
                    if syscalls_delta > 0:
                        syscalls_rate = syscalls_delta / wall_delta_sec

                    mach_msgs_delta = mach_msgs - prev.mach_msgs
                    if mach_msgs_delta > 0:
                        mach_msgs_rate = mach_msgs_delta / wall_delta_sec

                    wakeups_delta = wakeups - prev.wakeups
                    if wakeups_delta > 0:
                        wakeups_rate = wakeups_delta / wall_delta_sec

                    faults_delta = faults - prev.faults
                    if faults_delta > 0:
                        faults_rate = faults_delta / wall_delta_sec

                    # runnable_time is in mach units, convert to ms/sec
                    runnable_delta = runnable_time - prev.runnable_time
                    if runnable_delta > 0:
                        runnable_ns = abs_to_ns(runnable_delta, self._timebase)
                        runnable_time_rate = (runnable_ns / 1e6) / wall_delta_sec

                    # qos_interactive is in mach units, convert to ms/sec
                    qos_delta = qos_interactive - prev.qos_interactive
                    if qos_delta > 0:
                        qos_ns = abs_to_ns(qos_delta, self._timebase)
                        qos_interactive_rate = (qos_ns / 1e6) / wall_delta_sec

            # IPC (instructions per cycle) - no delta needed
            ipc = instructions / cycles if cycles > 0 else 0.0

            # Store current sample for next delta
            self._prev_samples[pid] = _PrevSample(
                cpu_time_ns=total_cpu_ns,
                disk_io=disk_io,
                energy=energy,
                timestamp=start,
                pageins=pageins,
                csw=csw,
                syscalls=syscalls,
                mach_msgs=mach_msgs,
                wakeups=wakeups,
                faults=faults,
                runnable_time=runnable_time,
                qos_interactive=qos_interactive,
            )

            # Get process name (try proc_name first, fall back to pbi_comm)
            command = get_process_name(pid)
            if not command:
                command = bsd_info.pbi_comm.decode("utf-8", errors="replace")
            if not command:
                command = f"pid_{pid}"

            # Map state
            state = get_state_name(bsd_info.pbi_status)

            # Build process dict with all metrics
            proc = {
                "pid": pid,
                "command": command,
                # CPU
                "cpu": cpu_percent,
                # Memory
                "mem": rusage.ri_phys_footprint,
                "mem_peak": rusage.ri_lifetime_max_phys_footprint,
                "pageins": pageins,
                "pageins_rate": pageins_rate,
                "faults": faults,
                "faults_rate": faults_rate,
                # Disk I/O
                "disk_io": disk_io,
                "disk_io_rate": disk_io_rate,
                # Activity
                "csw": csw,
                "csw_rate": csw_rate,
                "syscalls": syscalls,
                "syscalls_rate": syscalls_rate,
                "threads": task_info.pti_threadnum,
                "mach_msgs": mach_msgs,
                "mach_msgs_rate": mach_msgs_rate,
                # Efficiency
                "instructions": instructions,
                "cycles": cycles,
                "ipc": ipc,
                # Power
                "energy": energy,
                "energy_rate": energy_rate,
                "wakeups": wakeups,
                "wakeups_rate": wakeups_rate,
                # Contention
                "runnable_time": runnable_time,
                "runnable_time_rate": runnable_time_rate,
                "qos_interactive": qos_interactive,
                "qos_interactive_rate": qos_interactive_rate,
                # State
                "state": state,
                "priority": task_info.pti_priority,
            }
            all_processes.append(proc)

        # Prune stale PIDs from _prev_samples
        stale_pids = set(self._prev_samples.keys()) - current_pids
        for pid in stale_pids:
            del self._prev_samples[pid]

        # Score ALL processes first (cheap - just math), then select
        all_scored = [self._score_process(p) for p in all_processes]
        scored = self._select_rogues(all_scored)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        # Hybrid: max(peak, rms) - bad actors visible, cumulative stress can push higher
        scores = [p.score.high for p in scored]
        if scores:
            peak = max(scores)
            rms = int((sum(s * s for s in scores) / len(scores)) ** 0.5)
            max_score = max(peak, rms)
        else:
            max_score = 0

        return ProcessSamples(
            timestamp=datetime.now(),
            elapsed_ms=elapsed_ms,
            process_count=len(all_processes),
            max_score=max_score,
            rogues=scored,
        )

    async def collect(self) -> ProcessSamples:
        """Run collection in executor (syscalls are blocking)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._collect_sync)

    # ─────────────────────────────────────────────────────────────────────────

    def _select_rogues(self, scored: list[ProcessScore]) -> list[ProcessScore]:
        """Select top processes for display.

        Selection priority:
        1. Stuck processes (always first - these are critical)
        2. Remaining slots filled by highest-scoring processes

        Always returns up to max_count processes, ensuring TUI always has data.
        ProcessTracker independently decides which to persist based on tracking_threshold.
        """
        cfg = self.config.rogue_selection
        stuck: list[ProcessScore] = []
        rest: list[ProcessScore] = []

        for p in scored:
            if p.state.current == "stuck":
                stuck.append(p)
            else:
                rest.append(p)

        # Sort each group by score descending
        stuck.sort(key=lambda p: p.score.current, reverse=True)
        rest.sort(key=lambda p: p.score.current, reverse=True)

        # Combine: stuck first, then top scorers
        return (stuck + rest)[: cfg.max_count]

    def _get_band(self, score: int) -> str:
        """Derive band name from score using config thresholds."""
        return self.config.bands.get_band(score)

    def _make_metric(self, value: float | int) -> MetricValue:
        """Create a MetricValue with same current/low/high (daemon enriches later)."""
        return MetricValue(current=value, low=value, high=value)

    def _make_metric_str(self, value: str) -> MetricValueStr:
        """Create a MetricValueStr with same current/low/high (daemon enriches later)."""
        return MetricValueStr(current=value, low=value, high=value)

    def _get_dominant_metrics(self, proc: dict, category: str) -> list[str]:
        """Get human-readable descriptions of top metrics for the dominant category."""
        norm = self.config.scoring.normalization
        metrics = []

        if category == "blocking":
            # Check pageins_rate, disk_io_rate, faults_rate
            if proc["pageins_rate"] > 0:
                metrics.append(f"pageins:{int(proc['pageins_rate'])}/s")
            if proc["disk_io_rate"] > 0:
                rate = proc["disk_io_rate"]
                if rate >= 1024 * 1024:
                    metrics.append(f"disk:{rate / (1024 * 1024):.1f}M/s")
                elif rate >= 1024:
                    metrics.append(f"disk:{rate / 1024:.0f}K/s")
                else:
                    metrics.append(f"disk:{rate:.0f}B/s")
            if proc["faults_rate"] > 0:
                metrics.append(f"faults:{int(proc['faults_rate'])}/s")

        elif category == "contention":
            # Check runnable_time_rate, csw_rate, cpu
            if proc["runnable_time_rate"] > 0:
                metrics.append(f"runnable:{proc['runnable_time_rate']:.0f}ms/s")
            if proc["csw_rate"] > 0:
                val = proc["csw_rate"]
                if val >= 1000:
                    metrics.append(f"csw:{val / 1000:.1f}k/s")
                else:
                    metrics.append(f"csw:{val:.0f}/s")
            if proc["cpu"] > 0:
                metrics.append(f"cpu:{proc['cpu']:.0f}%")

        elif category == "pressure":
            # Check mem, wakeups_rate, syscalls_rate
            if proc["mem"] > 0:
                mem = proc["mem"]
                if mem >= 1024**3:
                    metrics.append(f"mem:{mem / (1024**3):.1f}G")
                else:
                    metrics.append(f"mem:{mem / (1024**2):.0f}M")
            if proc["wakeups_rate"] > 0:
                metrics.append(f"wakeups:{int(proc['wakeups_rate'])}/s")
            if proc["syscalls_rate"] > 0:
                val = proc["syscalls_rate"]
                if val >= 1000:
                    metrics.append(f"syscalls:{val / 1000:.1f}k/s")
                else:
                    metrics.append(f"syscalls:{val:.0f}/s")

        elif category == "efficiency":
            # Check ipc, threads
            if proc["ipc"] > 0 and proc["ipc"] < norm.ipc_min:
                metrics.append(f"ipc:{proc['ipc']:.2f}")
            if proc["threads"] > 10:
                metrics.append(f"threads:{proc['threads']}")

        return metrics[:3]  # Limit to top 3

    def _score_process(self, proc: dict) -> ProcessScore:
        """Compute 4-category stress scores, then weighted final score.

        Categories:
        - Blocking (40%): Things that CAUSE pauses (I/O, paging)
        - Contention (30%): Fighting for resources (scheduler pressure)
        - Pressure (20%): Stressing system resources (memory, syscalls)
        - Efficiency (10%): Wasting resources (stalled pipeline, too many threads)
        """
        norm = self.config.scoring.normalization
        multipliers = self.config.scoring.state_multipliers

        # ═══════════════════════════════════════════════════════════════════
        # BLOCKING SCORE (40% of final) - Things that CAUSE pauses
        # ═══════════════════════════════════════════════════════════════════
        if proc["state"] == "stuck":
            blocking_score = 100.0  # Automatic max for stuck processes
        else:
            blocking_score = (
                min(1.0, proc["pageins_rate"] / norm.pageins_rate) * 35
                + min(1.0, proc["disk_io_rate"] / norm.disk_io_rate) * 35
                + min(1.0, proc["faults_rate"] / norm.faults_rate) * 30
            )

        # ═══════════════════════════════════════════════════════════════════
        # CONTENTION SCORE (30% of final) - Fighting for resources
        # ═══════════════════════════════════════════════════════════════════
        contention_score = (
            min(1.0, proc["runnable_time_rate"] / norm.runnable_time_rate) * 30
            + min(1.0, proc["csw_rate"] / norm.csw_rate) * 30
            + min(1.0, proc["cpu"] / norm.cpu) * 25
            + min(1.0, proc["qos_interactive_rate"] / norm.qos_interactive_rate) * 15
        )

        # ═══════════════════════════════════════════════════════════════════
        # PRESSURE SCORE (20% of final) - Stressing system resources
        # ═══════════════════════════════════════════════════════════════════
        pressure_score = (
            min(1.0, proc["mem"] / (norm.mem_gb * 1024**3)) * 35
            + min(1.0, proc["wakeups_rate"] / norm.wakeups_rate) * 25
            + min(1.0, proc["syscalls_rate"] / norm.syscalls_rate) * 20
            + min(1.0, proc["mach_msgs_rate"] / norm.mach_msgs_rate) * 20
        )

        # ═══════════════════════════════════════════════════════════════════
        # EFFICIENCY SCORE (10% of final) - Wasting resources
        # ═══════════════════════════════════════════════════════════════════
        # Low IPC with high cycles = stalled pipeline (wasting CPU)
        ipc_penalty = (
            max(0.0, 1.0 - proc["ipc"] / norm.ipc_min) if proc["ipc"] < norm.ipc_min else 0.0
        )
        has_cycles = 1.0 if proc["cycles"] > 0 else 0.0
        efficiency_score = (ipc_penalty * has_cycles) * 60 + min(
            1.0, proc["threads"] / norm.threads
        ) * 40

        # ═══════════════════════════════════════════════════════════════════
        # FINAL SCORE - Weighted combination
        # ═══════════════════════════════════════════════════════════════════
        base_score = (
            blocking_score * 0.40
            + contention_score * 0.30
            + pressure_score * 0.20
            + efficiency_score * 0.10
        )

        # Apply state multiplier (discount for currently-inactive processes)
        state_mult = multipliers.get(proc["state"])
        final_score = min(100, int(base_score * state_mult))

        # Determine dominant category
        scores = {
            "blocking": blocking_score,
            "contention": contention_score,
            "pressure": pressure_score,
            "efficiency": efficiency_score,
        }
        dominant_category = max(scores, key=lambda k: scores[k])
        dominant_metrics = self._get_dominant_metrics(proc, dominant_category)

        band = self._get_band(final_score)
        captured_at = time.time()

        return ProcessScore(
            pid=proc["pid"],
            command=proc["command"],
            captured_at=captured_at,
            # CPU
            cpu=self._make_metric(proc["cpu"]),
            # Memory
            mem=self._make_metric(proc["mem"]),
            mem_peak=proc["mem_peak"],
            pageins=self._make_metric(proc["pageins"]),
            pageins_rate=self._make_metric(proc["pageins_rate"]),
            faults=self._make_metric(proc["faults"]),
            faults_rate=self._make_metric(proc["faults_rate"]),
            # Disk I/O
            disk_io=self._make_metric(proc["disk_io"]),
            disk_io_rate=self._make_metric(proc["disk_io_rate"]),
            # Activity
            csw=self._make_metric(proc["csw"]),
            csw_rate=self._make_metric(proc["csw_rate"]),
            syscalls=self._make_metric(proc["syscalls"]),
            syscalls_rate=self._make_metric(proc["syscalls_rate"]),
            threads=self._make_metric(proc["threads"]),
            mach_msgs=self._make_metric(proc["mach_msgs"]),
            mach_msgs_rate=self._make_metric(proc["mach_msgs_rate"]),
            # Efficiency
            instructions=self._make_metric(proc["instructions"]),
            cycles=self._make_metric(proc["cycles"]),
            ipc=self._make_metric(proc["ipc"]),
            # Power
            energy=self._make_metric(proc["energy"]),
            energy_rate=self._make_metric(proc["energy_rate"]),
            wakeups=self._make_metric(proc["wakeups"]),
            wakeups_rate=self._make_metric(proc["wakeups_rate"]),
            # Contention
            runnable_time=self._make_metric(proc["runnable_time"]),
            runnable_time_rate=self._make_metric(proc["runnable_time_rate"]),
            qos_interactive=self._make_metric(proc["qos_interactive"]),
            qos_interactive_rate=self._make_metric(proc["qos_interactive_rate"]),
            # State
            state=self._make_metric_str(proc["state"]),
            priority=self._make_metric(proc["priority"]),
            # Scoring
            score=self._make_metric(final_score),
            band=self._make_metric_str(band),
            blocking_score=self._make_metric(blocking_score),
            contention_score=self._make_metric(contention_score),
            pressure_score=self._make_metric(pressure_score),
            efficiency_score=self._make_metric(efficiency_score),
            dominant_category=dominant_category,
            dominant_metrics=dominant_metrics,
        )
