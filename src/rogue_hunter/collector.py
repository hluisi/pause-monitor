"""Process data collector using macOS top command."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog

from rogue_hunter.config import Config, ScoringConfig

# Type alias for dominant resource values
DominantResource = Literal["cpu", "gpu", "memory", "disk", "wakeups"]

log = structlog.get_logger()

# Severity orderings for categorical metrics
# Note: "halted" removed - not a real macOS process state
STATE_SEVERITY = {
    "idle": 0,
    "sleeping": 1,
    "running": 2,
    "stopped": 3,
    "zombie": 4,
    "stuck": 5,
}
BAND_SEVERITY = {
    "low": 0,
    "medium": 1,
    "elevated": 2,
    "high": 3,
    "critical": 4,
}


@dataclass
class ProcessScore:
    """Single process with metrics.

    This is THE canonical data schema for process data.
    DO NOT create alternative representations.
    """

    # ─────────────────────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────────────────────
    pid: int
    command: str
    captured_at: float

    # ─────────────────────────────────────────────────────────────
    # CPU
    # ─────────────────────────────────────────────────────────────
    cpu: float

    # ─────────────────────────────────────────────────────────────
    # Memory
    # ─────────────────────────────────────────────────────────────
    mem: int
    mem_peak: int  # Lifetime peak
    pageins: int  # Cumulative
    pageins_rate: float  # Page-ins per second
    faults: int  # Cumulative
    faults_rate: float  # Faults per second

    # ─────────────────────────────────────────────────────────────
    # Disk I/O
    # ─────────────────────────────────────────────────────────────
    disk_io: int
    disk_io_rate: float

    # ─────────────────────────────────────────────────────────────
    # Activity
    # ─────────────────────────────────────────────────────────────
    csw: int  # Cumulative context switches
    csw_rate: float  # Context switches per second
    syscalls: int  # Cumulative
    syscalls_rate: float  # Syscalls per second
    threads: int
    mach_msgs: int  # Cumulative
    mach_msgs_rate: float  # Messages per second

    # ─────────────────────────────────────────────────────────────
    # Efficiency
    # ─────────────────────────────────────────────────────────────
    instructions: int
    cycles: int
    ipc: float

    # ─────────────────────────────────────────────────────────────
    # Power
    # ─────────────────────────────────────────────────────────────
    energy: int
    energy_rate: float
    wakeups: int  # Cumulative
    wakeups_rate: float  # Wakeups per second

    # ─────────────────────────────────────────────────────────────
    # Contention (scheduler pressure indicators)
    # ─────────────────────────────────────────────────────────────
    runnable_time: int  # Cumulative runnable time (ns)
    runnable_time_rate: float  # ms runnable per second
    qos_interactive: int  # Cumulative QoS interactive time (ns)
    qos_interactive_rate: float  # ms interactive per second

    # ─────────────────────────────────────────────────────────────
    # GPU (WindowServer/GPU-related metrics)
    # ─────────────────────────────────────────────────────────────
    gpu_time: int  # Cumulative GPU time (ns)
    gpu_time_rate: float  # ms GPU per second

    # ─────────────────────────────────────────────────────────────
    # Zombie Children (parent not reaping - potential bug indicator)
    # ─────────────────────────────────────────────────────────────
    zombie_children: int  # Count of zombie child processes

    # ─────────────────────────────────────────────────────────────
    # State
    # ─────────────────────────────────────────────────────────────
    state: str
    priority: int

    # ─────────────────────────────────────────────────────────────
    # Scoring (resource-based system)
    # ─────────────────────────────────────────────────────────────
    score: int  # Final weighted score 0-100
    band: str  # low/medium/elevated/high/critical
    cpu_share: float  # Share of system CPU this process uses
    gpu_share: float  # Share of system GPU this process uses
    mem_share: float  # Share of system memory this process uses
    disk_share: float  # Share of system disk I/O this process uses
    wakeups_share: float  # Share of system wakeups this process causes
    disproportionality: float  # Highest resource share (max of above)
    dominant_resource: DominantResource  # Which resource this process dominates

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {
            "pid": self.pid,
            "command": self.command,
            "captured_at": self.captured_at,
            # CPU
            "cpu": self.cpu,
            # Memory
            "mem": self.mem,
            "mem_peak": self.mem_peak,
            "pageins": self.pageins,
            "pageins_rate": self.pageins_rate,
            "faults": self.faults,
            "faults_rate": self.faults_rate,
            # Disk I/O
            "disk_io": self.disk_io,
            "disk_io_rate": self.disk_io_rate,
            # Activity
            "csw": self.csw,
            "csw_rate": self.csw_rate,
            "syscalls": self.syscalls,
            "syscalls_rate": self.syscalls_rate,
            "threads": self.threads,
            "mach_msgs": self.mach_msgs,
            "mach_msgs_rate": self.mach_msgs_rate,
            # Efficiency
            "instructions": self.instructions,
            "cycles": self.cycles,
            "ipc": self.ipc,
            # Power
            "energy": self.energy,
            "energy_rate": self.energy_rate,
            "wakeups": self.wakeups,
            "wakeups_rate": self.wakeups_rate,
            # Contention
            "runnable_time": self.runnable_time,
            "runnable_time_rate": self.runnable_time_rate,
            "qos_interactive": self.qos_interactive,
            "qos_interactive_rate": self.qos_interactive_rate,
            # GPU
            "gpu_time": self.gpu_time,
            "gpu_time_rate": self.gpu_time_rate,
            # Zombie children
            "zombie_children": self.zombie_children,
            # State
            "state": self.state,
            "priority": self.priority,
            # Scoring
            "score": self.score,
            "band": self.band,
            "cpu_share": self.cpu_share,
            "gpu_share": self.gpu_share,
            "mem_share": self.mem_share,
            "disk_share": self.disk_share,
            "wakeups_share": self.wakeups_share,
            "disproportionality": self.disproportionality,
            "dominant_resource": self.dominant_resource,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessScore":
        """Deserialize from a dictionary."""
        return cls(
            pid=data["pid"],
            command=data["command"],
            captured_at=data["captured_at"],
            # CPU
            cpu=data["cpu"],
            # Memory
            mem=data["mem"],
            mem_peak=data["mem_peak"],
            pageins=data["pageins"],
            pageins_rate=data["pageins_rate"],
            faults=data["faults"],
            faults_rate=data["faults_rate"],
            # Disk I/O
            disk_io=data["disk_io"],
            disk_io_rate=data["disk_io_rate"],
            # Activity
            csw=data["csw"],
            csw_rate=data["csw_rate"],
            syscalls=data["syscalls"],
            syscalls_rate=data["syscalls_rate"],
            threads=data["threads"],
            mach_msgs=data["mach_msgs"],
            mach_msgs_rate=data["mach_msgs_rate"],
            # Efficiency
            instructions=data["instructions"],
            cycles=data["cycles"],
            ipc=data["ipc"],
            # Power
            energy=data["energy"],
            energy_rate=data["energy_rate"],
            wakeups=data["wakeups"],
            wakeups_rate=data["wakeups_rate"],
            # Contention
            runnable_time=data["runnable_time"],
            runnable_time_rate=data["runnable_time_rate"],
            qos_interactive=data["qos_interactive"],
            qos_interactive_rate=data["qos_interactive_rate"],
            # GPU
            gpu_time=data["gpu_time"],
            gpu_time_rate=data["gpu_time_rate"],
            # Zombie children
            zombie_children=data["zombie_children"],
            # State
            state=data["state"],
            priority=data["priority"],
            # Scoring
            score=data["score"],
            band=data["band"],
            cpu_share=data["cpu_share"],
            gpu_share=data["gpu_share"],
            mem_share=data["mem_share"],
            disk_share=data["disk_share"],
            wakeups_share=data["wakeups_share"],
            disproportionality=data["disproportionality"],
            dominant_resource=data["dominant_resource"],
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


def count_active_processes(processes: list[dict], config: ScoringConfig) -> int:
    """Count processes that are considered 'active' for fair share calculation.

    A process is active if:
    1. State is NOT idle (running, sleeping, stopped, zombie, stuck all count)
    2. AND using measurable resources (CPU > threshold OR memory > threshold OR disk I/O > 0)

    Returns at least 1 to avoid division by zero in fair share calculation.
    """
    count = 0
    mem_threshold_bytes = config.active_min_memory_mb * 1_048_576  # MiB (binary)

    for proc in processes:
        # Must be non-idle
        if proc.get("state") == "idle":
            continue

        # Must be using some resources
        cpu = proc.get("cpu", 0)
        mem = proc.get("mem", 0)
        disk_io_rate = proc.get("disk_io_rate", 0)

        uses_cpu = cpu >= config.active_min_cpu
        uses_memory = mem >= mem_threshold_bytes
        uses_disk = disk_io_rate > config.active_min_disk_io

        if uses_cpu or uses_memory or uses_disk:
            count += 1

    return max(1, count)  # Minimum 1 to avoid division by zero


def calculate_resource_shares(
    processes: list[dict],
    active_count: int,
) -> dict[int, dict[str, float]]:
    """Calculate resource shares for each process.

    For each resource type, calculates:
    1. Total system usage across all processes
    2. Fair share = 1 / active_count (as a fraction of total)
    3. Each process's share ratio = (process usage / total) / fair_share

    A share of 1.0 means the process uses exactly its fair share.
    A share of 10.0 means the process uses 10x its fair share.

    Returns dict mapping PID to dict of resource shares.
    """
    fair_share = 1.0 / active_count

    # Calculate totals
    total_cpu = sum(p.get("cpu", 0) for p in processes)
    total_gpu = sum(p.get("gpu_time_rate", 0) for p in processes)
    total_mem = sum(p.get("mem", 0) for p in processes)
    total_disk = sum(p.get("disk_io_rate", 0) for p in processes)
    total_wakeups = sum(p.get("wakeups_rate", 0) for p in processes)

    result = {}
    for proc in processes:
        pid = proc["pid"]

        # Calculate usage fraction for each resource (0.0 to 1.0)
        cpu_fraction = proc.get("cpu", 0) / total_cpu if total_cpu > 0 else 0
        gpu_fraction = proc.get("gpu_time_rate", 0) / total_gpu if total_gpu > 0 else 0
        mem_fraction = proc.get("mem", 0) / total_mem if total_mem > 0 else 0
        disk_fraction = proc.get("disk_io_rate", 0) / total_disk if total_disk > 0 else 0
        wakeups_fraction = proc.get("wakeups_rate", 0) / total_wakeups if total_wakeups > 0 else 0

        # Calculate share ratio (multiples of fair share)
        result[pid] = {
            "cpu_share": cpu_fraction / fair_share if fair_share > 0 else 0,
            "gpu_share": gpu_fraction / fair_share if fair_share > 0 else 0,
            "mem_share": mem_fraction / fair_share if fair_share > 0 else 0,
            "disk_share": disk_fraction / fair_share if fair_share > 0 else 0,
            "wakeups_share": wakeups_fraction / fair_share if fair_share > 0 else 0,
        }

    return result


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
    gpu_time: int  # Total GPU time (nanoseconds)


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
        from rogue_hunter.iokit import get_gpu_usage
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

        # Get GPU usage for all processes (one IORegistry scan per cycle)
        gpu_usage = get_gpu_usage()

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

            # Get GPU time for this process (0 if not using GPU)
            gpu_time = gpu_usage.get(pid, 0)

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
            gpu_time_rate = 0.0  # ms of GPU per second

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

                    # gpu_time is already in nanoseconds, convert to ms/sec
                    gpu_delta = gpu_time - prev.gpu_time
                    if gpu_delta > 0:
                        gpu_time_rate = (gpu_delta / 1e6) / wall_delta_sec

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
                gpu_time=gpu_time,
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
                "ppid": bsd_info.pbi_ppid,  # Parent PID (for zombie counting)
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
                # GPU
                "gpu_time": gpu_time,
                "gpu_time_rate": gpu_time_rate,
                # State
                "state": state,
                "priority": task_info.pti_priority,
            }
            all_processes.append(proc)

        # Prune stale PIDs from _prev_samples
        stale_pids = set(self._prev_samples.keys()) - current_pids
        for pid in stale_pids:
            del self._prev_samples[pid]

        # Count zombie children per parent (for pressure scoring)
        # A process with many zombie children isn't reaping them = potential bug
        zombie_count: dict[int, int] = {}
        for proc in all_processes:
            if proc["state"] == "zombie":
                ppid = proc["ppid"]
                zombie_count[ppid] = zombie_count.get(ppid, 0) + 1

        # Backfill zombie_children into each process dict
        for proc in all_processes:
            proc["zombie_children"] = zombie_count.get(proc["pid"], 0)

        # Score ALL processes first (cheap - just math), then select
        all_scored = [self._score_process(p) for p in all_processes]
        scored = self._select_rogues(all_scored)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        # Hybrid: max(peak, rms) - bad actors visible, cumulative stress can push higher
        scores = [p.score for p in scored]
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
            if p.state == "stuck":
                stuck.append(p)
            else:
                rest.append(p)

        # Sort each group by score descending
        stuck.sort(key=lambda p: p.score, reverse=True)
        rest.sort(key=lambda p: p.score, reverse=True)

        # Combine: stuck first, then top scorers
        return (stuck + rest)[: cfg.max_count]

    def _get_band(self, score: int) -> str:
        """Derive band name from score using config thresholds."""
        return self.config.bands.get_band(score)

    def _get_dominant_metrics(self, proc: dict, category: str) -> list[str]:
        """Get human-readable descriptions of top metrics for the dominant category."""
        norm = self.config.scoring.normalization
        metrics = []

        if category == "blocking":
            # Check pageins_rate, disk_io_rate, faults_rate, gpu_time_rate
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
            if proc["gpu_time_rate"] > 0:
                metrics.append(f"gpu:{proc['gpu_time_rate']:.0f}ms/s")

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
            # Check mem, wakeups_rate, syscalls_rate, zombie_children
            if proc["zombie_children"] > 0:
                # Show first since unreaped zombies are unusual/significant
                metrics.append(f"zombies:{proc['zombie_children']}")
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
                min(1.0, proc["pageins_rate"] / norm.pageins_rate) * 30
                + min(1.0, proc["disk_io_rate"] / norm.disk_io_rate) * 30
                + min(1.0, proc["faults_rate"] / norm.faults_rate) * 20
                + min(1.0, proc["gpu_time_rate"] / norm.gpu_time_rate) * 20
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
            min(1.0, proc["mem"] / (norm.mem_gb * 1024**3)) * 30
            + min(1.0, proc["wakeups_rate"] / norm.wakeups_rate) * 25
            + min(1.0, proc["syscalls_rate"] / norm.syscalls_rate) * 15
            + min(1.0, proc["mach_msgs_rate"] / norm.mach_msgs_rate) * 15
            + min(1.0, proc["zombie_children"] / norm.zombie_children) * 15
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

        # Determine dominant resource based on category scores
        # Map old categories to new resource names for compatibility
        category_to_resource: dict[str, DominantResource] = {
            "blocking": "disk",  # Blocking is I/O-related
            "contention": "cpu",  # Contention is CPU scheduler pressure
            "pressure": "memory",  # Pressure is memory/syscalls
            "efficiency": "cpu",  # Efficiency issues show up as CPU waste
        }
        scores = {
            "blocking": blocking_score,
            "contention": contention_score,
            "pressure": pressure_score,
            "efficiency": efficiency_score,
        }
        dominant_category = max(scores, key=lambda k: scores[k])
        dominant_resource = category_to_resource[dominant_category]

        # Temporary placeholder values for resource shares
        # Will be properly implemented in Task 6 (fair share calculation)
        cpu_share = proc["cpu"] / 100.0  # Normalize CPU% to share
        gpu_share = proc["gpu_time_rate"] / 1000.0 if proc["gpu_time_rate"] > 0 else 0.0
        mem_share = 0.0  # Requires total system memory context
        disk_share = 0.0  # Requires system-wide disk I/O context
        wakeups_share = 0.0  # Requires system-wide wakeups context

        # Disproportionality is the max share
        disproportionality = max(cpu_share, gpu_share, mem_share, disk_share, wakeups_share)

        band = self._get_band(final_score)
        captured_at = time.time()

        return ProcessScore(
            pid=proc["pid"],
            command=proc["command"],
            captured_at=captured_at,
            # CPU
            cpu=proc["cpu"],
            # Memory
            mem=proc["mem"],
            mem_peak=proc["mem_peak"],
            pageins=proc["pageins"],
            pageins_rate=proc["pageins_rate"],
            faults=proc["faults"],
            faults_rate=proc["faults_rate"],
            # Disk I/O
            disk_io=proc["disk_io"],
            disk_io_rate=proc["disk_io_rate"],
            # Activity
            csw=proc["csw"],
            csw_rate=proc["csw_rate"],
            syscalls=proc["syscalls"],
            syscalls_rate=proc["syscalls_rate"],
            threads=proc["threads"],
            mach_msgs=proc["mach_msgs"],
            mach_msgs_rate=proc["mach_msgs_rate"],
            # Efficiency
            instructions=proc["instructions"],
            cycles=proc["cycles"],
            ipc=proc["ipc"],
            # Power
            energy=proc["energy"],
            energy_rate=proc["energy_rate"],
            wakeups=proc["wakeups"],
            wakeups_rate=proc["wakeups_rate"],
            # Contention
            runnable_time=proc["runnable_time"],
            runnable_time_rate=proc["runnable_time_rate"],
            qos_interactive=proc["qos_interactive"],
            qos_interactive_rate=proc["qos_interactive_rate"],
            # GPU
            gpu_time=proc["gpu_time"],
            gpu_time_rate=proc["gpu_time_rate"],
            # Zombie children
            zombie_children=proc["zombie_children"],
            # State
            state=proc["state"],
            priority=proc["priority"],
            # Scoring
            score=final_score,
            band=band,
            cpu_share=cpu_share,
            gpu_share=gpu_share,
            mem_share=mem_share,
            disk_share=disk_share,
            wakeups_share=wakeups_share,
            disproportionality=disproportionality,
            dominant_resource=dominant_resource,
        )
