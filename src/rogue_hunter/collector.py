"""Process data collector using macOS top command."""

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog

from rogue_hunter.config import Config, ResourceWeights

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
    rogues: list[ProcessScore]  # Top-N for TUI display
    all_by_pid: dict[int, ProcessScore]  # All scored processes by PID

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
        rogues = [ProcessScore.from_dict(r) for r in d["rogues"]]
        return cls(
            timestamp=datetime.fromisoformat(d["timestamp"]),
            elapsed_ms=d["elapsed_ms"],
            process_count=d["process_count"],
            max_score=d["max_score"],
            rogues=rogues,
            all_by_pid={r.pid: r for r in rogues},  # Reconstruct from rogues
        )


def calculate_resource_shares(
    processes: list[dict],
    share_min_cpu: float = 0.01,
    share_min_memory_bytes: int = 268_435_456,
    share_min_wakeups: float = 10.0,
) -> dict[int, dict[str, float]]:
    """Calculate resource shares for each process using per-resource fair share.

    For each resource type, calculates:
    1. Count processes actively using THAT SPECIFIC resource
    2. Fair share = 1 / active_users_of_resource
    3. Each process's share ratio = (process usage / total) / fair_share

    Key insight: fair share is calculated PER RESOURCE. If 100 processes exist
    but only 2 use disk I/O, fair share for disk is 1/2 = 50%, not 1/100 = 1%.
    This prevents flagging a process as "disproportionate" just because it's
    one of few using that resource type.

    A share of 1.0 means the process uses exactly its fair share among users.
    A share of 2.0 means the process uses 2x its fair share (in a 2-user pool).

    Args:
        processes: List of process dicts with resource metrics
        share_min_cpu: CPU % threshold to count as resource user
        share_min_memory_bytes: Memory bytes threshold for resource user
        share_min_wakeups: Wakeups/sec threshold for resource user

    Returns dict mapping PID to dict of resource shares.
    """
    # First pass: calculate totals and count active users per resource
    total_cpu = 0.0
    total_gpu = 0.0
    total_mem = 0
    total_disk = 0.0
    total_wakeups = 0.0
    cpu_users = 0
    gpu_users = 0
    mem_users = 0
    disk_users = 0
    wakeups_users = 0

    for proc in processes:
        cpu = proc.get("cpu", 0)
        gpu = proc.get("gpu_time_rate", 0)
        mem = proc.get("mem", 0)
        disk = proc.get("disk_io_rate", 0)
        wakeups = proc.get("wakeups_rate", 0)

        total_cpu += cpu
        total_gpu += gpu
        total_mem += mem
        total_disk += disk
        total_wakeups += wakeups

        # Count processes with non-trivial usage of each resource
        # Using configurable thresholds to filter measurement noise
        if cpu > share_min_cpu:
            cpu_users += 1
        if gpu > 0:  # Any GPU usage
            gpu_users += 1
        if mem > share_min_memory_bytes:
            mem_users += 1
        if disk > 0:  # Any disk I/O
            disk_users += 1
        if wakeups > share_min_wakeups:
            wakeups_users += 1

    # Fair share per resource (minimum 1 to avoid division by zero)
    cpu_fair = 1.0 / max(1, cpu_users)
    gpu_fair = 1.0 / max(1, gpu_users)
    mem_fair = 1.0 / max(1, mem_users)
    disk_fair = 1.0 / max(1, disk_users)
    wakeups_fair = 1.0 / max(1, wakeups_users)

    # Second pass: calculate shares for each process
    result = {}
    for proc in processes:
        pid = proc["pid"]

        # Calculate usage fraction for each resource (0.0 to 1.0)
        cpu_fraction = proc.get("cpu", 0) / total_cpu if total_cpu > 0 else 0
        gpu_fraction = proc.get("gpu_time_rate", 0) / total_gpu if total_gpu > 0 else 0
        mem_fraction = proc.get("mem", 0) / total_mem if total_mem > 0 else 0
        disk_fraction = proc.get("disk_io_rate", 0) / total_disk if total_disk > 0 else 0
        wakeups_fraction = proc.get("wakeups_rate", 0) / total_wakeups if total_wakeups > 0 else 0

        # Calculate share ratio (multiples of fair share for that resource)
        result[pid] = {
            "cpu_share": cpu_fraction / cpu_fair,
            "gpu_share": gpu_fraction / gpu_fair,
            "mem_share": mem_fraction / mem_fair,
            "disk_share": disk_fraction / disk_fair,
            "wakeups_share": wakeups_fraction / wakeups_fair,
        }

    return result


def score_from_shares(
    shares: dict[str, float],
    weights: ResourceWeights,
    curve_multiplier: float = 10.0,
) -> tuple[int, DominantResource, float]:
    """Calculate score from resource shares using Apple-style weighting.

    Uses logarithmic curve to map disproportionality to 0-100 score:
    - High band (50-69) reachable at ~50-100x fair share
    - Critical band (70+) reachable at ~200x fair share

    Args:
        shares: Dict with cpu_share, gpu_share, mem_share, disk_share, wakeups_share
        weights: ResourceWeights with weight multipliers for each resource
        curve_multiplier: Multiplier for log2 scoring curve (default 10.0)

    Returns:
        Tuple of (score 0-100, dominant_resource, disproportionality)
    """
    # Calculate weighted contributions
    weighted: dict[DominantResource, float] = {
        "cpu": shares["cpu_share"] * weights.cpu,
        "gpu": shares["gpu_share"] * weights.gpu,
        "memory": shares["mem_share"] * weights.memory,
        "disk": shares["disk_share"] * weights.disk_io,
        "wakeups": shares["wakeups_share"] * weights.wakeups,
    }

    # Find dominant resource (highest weighted contribution)
    dominant: DominantResource = max(weighted, key=lambda k: weighted[k])

    # Map resource name to share key for disproportionality
    share_key_map: dict[DominantResource, str] = {
        "cpu": "cpu_share",
        "gpu": "gpu_share",
        "memory": "mem_share",
        "disk": "disk_share",
        "wakeups": "wakeups_share",
    }
    disproportionality = shares[share_key_map[dominant]]

    # Sum weighted contributions
    total_weighted = sum(weighted.values())

    # Apply logarithmic curve
    # log2(1) = 0, log2(2) = 1, log2(50) ≈ 5.6, log2(100) ≈ 6.6, log2(200) ≈ 7.6
    # Scale: multiply by curve_multiplier to get score range
    # Target with multiplier=10: 50x -> ~56, 100x -> ~66 (high band), 200x -> ~76 (critical)
    if total_weighted <= 1.0:
        # At or below fair share = score 0
        raw_score = 0.0
    else:
        # Logarithmic scaling
        raw_score = math.log2(total_weighted) * curve_multiplier

    # Clamp to 0-100
    score = max(0, min(100, int(raw_score)))

    return score, dominant, disproportionality


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

        # Calculate fair shares for resource-based scoring (per-resource active counts)
        scoring = self.config.scoring
        shares_by_pid = calculate_resource_shares(
            all_processes,
            share_min_cpu=scoring.share_min_cpu,
            share_min_memory_bytes=scoring.share_min_memory_bytes,
            share_min_wakeups=scoring.share_min_wakeups,
        )

        # Score ALL processes first (cheap - just math), then select
        all_scored = [
            self._score_process(p, shares_by_pid.get(p["pid"], {})) for p in all_processes
        ]

        # All scores by PID (daemon uses this to build tracker input)
        all_by_pid = {p.pid: p for p in all_scored}

        # Rogues: top-N for TUI display
        rogues = self._select_rogues(all_scored)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        # Hybrid: max(peak, rms) - bad actors visible, cumulative stress can push higher
        scores = [p.score for p in rogues]
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
            rogues=rogues,
            all_by_pid=all_by_pid,
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

    def _score_process(self, proc: dict, shares: dict[str, float]) -> ProcessScore:
        """Score a process using resource-based fair share analysis.

        Uses the new scoring system based on how much of each resource
        a process consumes relative to its fair share.
        """
        multipliers = self.config.scoring.state_multipliers
        weights = self.config.scoring.resource_weights

        # Default shares if not provided (e.g., process disappeared between collect and score)
        default_shares = {
            "cpu_share": 0.0,
            "gpu_share": 0.0,
            "mem_share": 0.0,
            "disk_share": 0.0,
            "wakeups_share": 0.0,
        }
        shares = shares if shares else default_shares

        # Calculate score from resource shares
        curve_multiplier = self.config.scoring.score_curve_multiplier
        base_score, dominant_resource, disproportionality = score_from_shares(
            shares, weights, curve_multiplier
        )

        # Apply state multiplier (discount for currently-inactive processes)
        state_mult = multipliers.get(proc["state"])
        final_score = max(0, min(100, int(base_score * state_mult)))

        # Get band from config
        band = self.config.bands.get_band(final_score)
        captured_at = time.time()

        # Extract share values
        cpu_share = shares.get("cpu_share", 0.0)
        gpu_share = shares.get("gpu_share", 0.0)
        mem_share = shares.get("mem_share", 0.0)
        disk_share = shares.get("disk_share", 0.0)
        wakeups_share = shares.get("wakeups_share", 0.0)

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
