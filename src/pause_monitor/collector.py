"""Process data collector using macOS top command."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

from pause_monitor.config import Config

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
    pageins: MetricValue
    faults: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Disk I/O
    # ─────────────────────────────────────────────────────────────
    disk_io: MetricValue
    disk_io_rate: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Activity
    # ─────────────────────────────────────────────────────────────
    csw: MetricValue
    syscalls: MetricValue
    threads: MetricValue
    mach_msgs: MetricValue

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
    wakeups: MetricValue

    # ─────────────────────────────────────────────────────────────
    # State (categorical with hierarchy)
    # ─────────────────────────────────────────────────────────────
    state: MetricValueStr
    priority: MetricValue

    # ─────────────────────────────────────────────────────────────
    # Scoring (our assessment)
    # ─────────────────────────────────────────────────────────────
    score: MetricValue
    band: MetricValueStr
    categories: list[str]

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
            "faults": self.faults.to_dict(),
            # Disk I/O
            "disk_io": self.disk_io.to_dict(),
            "disk_io_rate": self.disk_io_rate.to_dict(),
            # Activity
            "csw": self.csw.to_dict(),
            "syscalls": self.syscalls.to_dict(),
            "threads": self.threads.to_dict(),
            "mach_msgs": self.mach_msgs.to_dict(),
            # Efficiency
            "instructions": self.instructions.to_dict(),
            "cycles": self.cycles.to_dict(),
            "ipc": self.ipc.to_dict(),
            # Power
            "energy": self.energy.to_dict(),
            "energy_rate": self.energy_rate.to_dict(),
            "wakeups": self.wakeups.to_dict(),
            # State
            "state": self.state.to_dict(),
            "priority": self.priority.to_dict(),
            # Scoring
            "score": self.score.to_dict(),
            "band": self.band.to_dict(),
            "categories": self.categories,
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
            faults=MetricValue.from_dict(data["faults"]),
            # Disk I/O
            disk_io=MetricValue.from_dict(data["disk_io"]),
            disk_io_rate=MetricValue.from_dict(data["disk_io_rate"]),
            # Activity
            csw=MetricValue.from_dict(data["csw"]),
            syscalls=MetricValue.from_dict(data["syscalls"]),
            threads=MetricValue.from_dict(data["threads"]),
            mach_msgs=MetricValue.from_dict(data["mach_msgs"]),
            # Efficiency
            instructions=MetricValue.from_dict(data["instructions"]),
            cycles=MetricValue.from_dict(data["cycles"]),
            ipc=MetricValue.from_dict(data["ipc"]),
            # Power
            energy=MetricValue.from_dict(data["energy"]),
            energy_rate=MetricValue.from_dict(data["energy_rate"]),
            wakeups=MetricValue.from_dict(data["wakeups"]),
            # State
            state=MetricValueStr.from_dict(data["state"]),
            priority=MetricValue.from_dict(data["priority"]),
            # Scoring
            score=MetricValue.from_dict(data["score"]),
            band=MetricValueStr.from_dict(data["band"]),
            categories=data["categories"],
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
            faults=MetricValue.from_dict(data["faults"]),
            # Disk I/O
            disk_io=MetricValue.from_dict(data["disk_io"]),
            disk_io_rate=MetricValue.from_dict(data["disk_io_rate"]),
            # Activity
            csw=MetricValue.from_dict(data["csw"]),
            syscalls=MetricValue.from_dict(data["syscalls"]),
            threads=MetricValue.from_dict(data["threads"]),
            mach_msgs=MetricValue.from_dict(data["mach_msgs"]),
            # Efficiency
            instructions=MetricValue.from_dict(data["instructions"]),
            cycles=MetricValue.from_dict(data["cycles"]),
            ipc=MetricValue.from_dict(data["ipc"]),
            # Power
            energy=MetricValue.from_dict(data["energy"]),
            energy_rate=MetricValue.from_dict(data["energy_rate"]),
            wakeups=MetricValue.from_dict(data["wakeups"]),
            # State
            state=MetricValueStr.from_dict(data["state"]),
            priority=MetricValue.from_dict(data["priority"]),
            # Scoring
            score=MetricValue.from_dict(data["score"]),
            band=MetricValueStr.from_dict(data["band"]),
            categories=data["categories"],
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
    """Previous sample state for delta calculations (CPU%, disk rate, energy rate)."""

    cpu_time_ns: int  # Total CPU time (user + system) in nanoseconds
    disk_io: int  # Total disk I/O bytes (read + write)
    energy: int  # Total energy billed
    timestamp: float  # time.monotonic() when sampled


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
        from pause_monitor.libproc import get_timebase_info

        self._timebase = get_timebase_info()

    def _collect_sync(self) -> ProcessSamples:
        """Synchronous collection - runs in executor."""
        from pause_monitor.libproc import (
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

            # Calculate deltas/rates from previous sample
            cpu_percent = 0.0
            disk_io_rate = 0.0
            energy_rate = 0.0
            if self._last_collect_time > 0:
                wall_delta_sec = start - self._last_collect_time
            else:
                wall_delta_sec = 0.0

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

            # IPC (instructions per cycle) - no delta needed
            ipc = instructions / cycles if cycles > 0 else 0.0

            # Store current sample for next delta
            self._prev_samples[pid] = _PrevSample(
                cpu_time_ns=total_cpu_ns,
                disk_io=disk_io,
                energy=energy,
                timestamp=start,
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
                "pageins": rusage.ri_pageins,
                "faults": task_info.pti_faults,
                # Disk I/O
                "disk_io": disk_io,
                "disk_io_rate": disk_io_rate,
                # Activity
                "csw": task_info.pti_csw,
                "syscalls": task_info.pti_syscalls_mach + task_info.pti_syscalls_unix,
                "threads": task_info.pti_threadnum,
                "mach_msgs": task_info.pti_messages_sent + task_info.pti_messages_received,
                # Efficiency
                "instructions": instructions,
                "cycles": cycles,
                "ipc": ipc,
                # Power
                "energy": energy,
                "energy_rate": energy_rate,
                "wakeups": rusage.ri_pkg_idle_wkups + rusage.ri_interrupt_wkups,
                # State
                "state": state,
                "priority": task_info.pti_priority,
            }
            all_processes.append(proc)

        # Prune stale PIDs from _prev_samples
        stale_pids = set(self._prev_samples.keys()) - current_pids
        for pid in stale_pids:
            del self._prev_samples[pid]

        rogues = self._select_rogues(all_processes)
        scored = [self._score_process(p) for p in rogues]

        elapsed_ms = int((time.monotonic() - start) * 1000)
        # Hybrid: max(peak_score, rms) - single bad actor never hidden, cumulative stress can push higher
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

    def _select_rogues(self, processes: list[dict]) -> list[dict]:
        """Apply rogue selection rules from config.

        Returns a list of process dicts with an added '_categories' set
        indicating why each process was selected.
        """
        selected: dict[int, dict] = {}  # pid -> process with _categories

        # 1. Always include stuck (hardcoded, not configurable)
        for proc in processes:
            if proc["state"] == "stuck":
                pid = proc["pid"]
                if pid not in selected:
                    selected[pid] = {**proc, "_categories": set()}
                selected[pid]["_categories"].add("stuck")

        # 2. Include configured states (zombie, etc.) - excluding stuck which is already handled
        state_cfg = self.config.rogue_selection.state
        if state_cfg.enabled:
            matching = [
                p for p in processes if p["state"] in state_cfg.states and p["state"] != "stuck"
            ]
            if state_cfg.count > 0:
                matching = matching[: state_cfg.count]
            for proc in matching:
                pid = proc["pid"]
                if pid not in selected:
                    selected[pid] = {**proc, "_categories": set()}
                selected[pid]["_categories"].add("state")

        # 3. Top N per enabled category above threshold
        categories = [
            ("cpu", "cpu", self.config.rogue_selection.cpu),
            ("mem", "mem", self.config.rogue_selection.mem),
            ("threads", "threads", self.config.rogue_selection.threads),
            ("csw", "csw", self.config.rogue_selection.csw),
            ("syscalls", "syscalls", self.config.rogue_selection.syscalls),
            ("pageins", "pageins", self.config.rogue_selection.pageins),
        ]

        for cat_name, metric, cfg in categories:
            if not cfg.enabled:
                continue

            # Filter by threshold and sort
            eligible = [p for p in processes if p[metric] > cfg.threshold]
            eligible.sort(key=lambda p: p[metric], reverse=True)

            # Take top N
            for proc in eligible[: cfg.count]:
                pid = proc["pid"]
                if pid not in selected:
                    selected[pid] = {**proc, "_categories": set()}
                selected[pid]["_categories"].add(cat_name)

        return list(selected.values())

    def _normalize_state(self, state: str) -> float:
        """Normalize state to 0-1 scale for base score calculation."""
        if state == "stuck":
            return 1.0
        elif state == "zombie":
            return 0.8
        elif state == "halted":
            return 0.6
        elif state == "stopped":
            return 0.4
        else:
            return 0.0

    def _get_band(self, score: int) -> str:
        """Derive band name from score using config thresholds."""
        return self.config.bands.get_band(score)

    def _make_metric(self, value: float | int) -> MetricValue:
        """Create a MetricValue with same current/low/high (daemon enriches later)."""
        return MetricValue(current=value, low=value, high=value)

    def _make_metric_str(self, value: str) -> MetricValueStr:
        """Create a MetricValueStr with same current/low/high (daemon enriches later)."""
        return MetricValueStr(current=value, low=value, high=value)

    def _score_process(self, proc: dict) -> ProcessScore:
        """Compute stressor score using config weights, then apply state multiplier."""
        weights = self.config.scoring.weights
        multipliers = self.config.scoring.state_multipliers
        norm = self.config.scoring.normalization

        # Normalize each metric to 0-1 scale using configurable maximums
        normalized = {
            "cpu": min(1.0, proc["cpu"] / norm.cpu),
            "state": self._normalize_state(proc["state"]),
            "pageins": min(1.0, proc["pageins"] / norm.pageins),
            "mem": min(1.0, proc["mem"] / (norm.mem_gb * 1024**3)),
            "csw": min(1.0, proc["csw"] / norm.csw),
            "syscalls": min(1.0, proc["syscalls"] / norm.syscalls),
            "threads": min(1.0, proc["threads"] / norm.threads),
            "disk_io_rate": min(1.0, proc["disk_io_rate"] / norm.disk_io_rate),
            "energy_rate": min(1.0, proc["energy_rate"] / norm.energy_rate),
            "wakeups": min(1.0, proc["wakeups"] / norm.wakeups),
            # IPC: inverse scoring — low IPC is bad (stalled pipeline)
            "ipc": (
                max(0.0, 1.0 - (proc["ipc"] / norm.ipc_min)) if proc["ipc"] < norm.ipc_min else 0.0
            ),
        }

        # Weighted sum (base score - what this process WOULD contribute if active)
        base_score = (
            normalized["cpu"] * weights.cpu
            + normalized["state"] * weights.state
            + normalized["pageins"] * weights.pageins
            + normalized["mem"] * weights.mem
            + normalized["csw"] * weights.csw
            + normalized["syscalls"] * weights.syscalls
            + normalized["threads"] * weights.threads
            + normalized["disk_io_rate"] * weights.disk_io_rate
            + normalized["energy_rate"] * weights.energy_rate
            + normalized["wakeups"] * weights.wakeups
            + normalized["ipc"] * weights.ipc
        )

        # Apply state multiplier (discount for currently-inactive processes)
        state_mult = multipliers.get(proc["state"])

        # Multi-category bonus: processes triggering 3+ categories are more suspicious
        category_count = len(proc["_categories"])
        category_bonus = 1.0 + (0.1 * max(0, category_count - 2))

        score = min(100, int(base_score * state_mult * category_bonus))
        band = self._get_band(score)
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
            faults=self._make_metric(proc["faults"]),
            # Disk I/O
            disk_io=self._make_metric(proc["disk_io"]),
            disk_io_rate=self._make_metric(proc["disk_io_rate"]),
            # Activity
            csw=self._make_metric(proc["csw"]),
            syscalls=self._make_metric(proc["syscalls"]),
            threads=self._make_metric(proc["threads"]),
            mach_msgs=self._make_metric(proc["mach_msgs"]),
            # Efficiency
            instructions=self._make_metric(proc["instructions"]),
            cycles=self._make_metric(proc["cycles"]),
            ipc=self._make_metric(proc["ipc"]),
            # Power
            energy=self._make_metric(proc["energy"]),
            energy_rate=self._make_metric(proc["energy_rate"]),
            wakeups=self._make_metric(proc["wakeups"]),
            # State
            state=self._make_metric_str(proc["state"]),
            priority=self._make_metric(proc["priority"]),
            # Scoring
            score=self._make_metric(score),
            band=self._make_metric_str(band),
            categories=list(proc["_categories"]),
        )
