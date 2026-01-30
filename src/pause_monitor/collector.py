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


@dataclass
class ProcessScore:
    """Single process with its stressor score."""

    pid: int
    command: str
    cpu: float
    state: str
    mem: int
    cmprs: int
    pageins: int
    csw: int
    sysbsd: int
    threads: int
    score: int
    categories: frozenset[str]
    captured_at: float

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {
            "pid": self.pid,
            "command": self.command,
            "cpu": self.cpu,
            "state": self.state,
            "mem": self.mem,
            "cmprs": self.cmprs,
            "pageins": self.pageins,
            "csw": self.csw,
            "sysbsd": self.sysbsd,
            "threads": self.threads,
            "score": self.score,
            "categories": list(self.categories),
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessScore":
        """Deserialize from a dictionary."""
        return cls(
            pid=data["pid"],
            command=data["command"],
            cpu=data["cpu"],
            state=data["state"],
            mem=data["mem"],
            cmprs=data["cmprs"],
            pageins=data["pageins"],
            csw=data["csw"],
            sysbsd=data["sysbsd"],
            threads=data["threads"],
            score=data["score"],
            categories=frozenset(data["categories"]),
            captured_at=data["captured_at"],
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
    """Previous sample state for CPU% delta calculation."""

    cpu_time_ns: int  # Total CPU time (user + system) in nanoseconds
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

            # Calculate CPU% from delta
            cpu_percent = 0.0
            if wall_delta_ns > 0 and pid in self._prev_samples:
                prev = self._prev_samples[pid]
                cpu_delta_ns = total_cpu_ns - prev.cpu_time_ns
                if cpu_delta_ns > 0:
                    # CPU% = (CPU time used / wall time elapsed) * 100
                    cpu_percent = (cpu_delta_ns / wall_delta_ns) * 100.0

            # Store current sample for next delta
            self._prev_samples[pid] = _PrevSample(cpu_time_ns=total_cpu_ns, timestamp=start)

            # Get process name (try proc_name first, fall back to pbi_comm)
            command = get_process_name(pid)
            if not command:
                command = bsd_info.pbi_comm.decode("utf-8", errors="replace")
            if not command:
                command = f"pid_{pid}"

            # Map state
            state = get_state_name(bsd_info.pbi_status)

            # Build process dict
            proc = {
                "pid": pid,
                "command": command,
                "cpu": cpu_percent,
                "state": state,
                "mem": rusage.ri_phys_footprint,  # Physical footprint (Activity Monitor "Memory")
                "cmprs": 0,  # Not available without expensive API
                "pageins": rusage.ri_pageins,
                "csw": task_info.pti_csw,
                "sysbsd": task_info.pti_syscalls_mach + task_info.pti_syscalls_unix,
                "threads": task_info.pti_threadnum,
            }
            all_processes.append(proc)

        # Prune stale PIDs from _prev_samples
        stale_pids = set(self._prev_samples.keys()) - current_pids
        for pid in stale_pids:
            del self._prev_samples[pid]

        rogues = self._select_rogues(all_processes)
        scored = [self._score_process(p) for p in rogues]

        elapsed_ms = int((time.monotonic() - start) * 1000)
        max_score = max((p.score for p in scored), default=0)

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
            ("cmprs", "cmprs", self.config.rogue_selection.cmprs),
            ("threads", "threads", self.config.rogue_selection.threads),
            ("csw", "csw", self.config.rogue_selection.csw),
            ("sysbsd", "sysbsd", self.config.rogue_selection.sysbsd),
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
            "cmprs": min(1.0, proc["cmprs"] / (norm.cmprs_gb * 1024**3)),
            "csw": min(1.0, proc["csw"] / norm.csw),
            "sysbsd": min(1.0, proc["sysbsd"] / norm.sysbsd),
            "threads": min(1.0, proc["threads"] / norm.threads),
        }

        # Weighted sum (base score - what this process WOULD contribute if active)
        base_score = (
            normalized["cpu"] * weights.cpu
            + normalized["state"] * weights.state
            + normalized["pageins"] * weights.pageins
            + normalized["mem"] * weights.mem
            + normalized["cmprs"] * weights.cmprs
            + normalized["csw"] * weights.csw
            + normalized["sysbsd"] * weights.sysbsd
            + normalized["threads"] * weights.threads
        )

        # Apply state multiplier (discount for currently-inactive processes)
        state_mult = multipliers.get(proc["state"])

        # Multi-category bonus: processes triggering 3+ categories are more suspicious
        category_count = len(proc["_categories"])
        category_bonus = 1.0 + (0.1 * max(0, category_count - 2))

        score = min(100, int(base_score * state_mult * category_bonus))

        return ProcessScore(
            pid=proc["pid"],
            command=proc["command"],
            cpu=proc["cpu"],
            state=proc["state"],
            mem=proc["mem"],
            cmprs=proc["cmprs"],
            pageins=proc["pageins"],
            csw=proc["csw"],
            sysbsd=proc["sysbsd"],
            threads=proc["threads"],
            score=score,
            categories=frozenset(proc["_categories"]),
            captured_at=time.time(),
        )
