"""Process data collector using macOS top command."""

import asyncio
import json
import os
import re
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


class TopCollector:
    """Collects process data via top command at 1Hz."""

    def __init__(self, config: Config):
        self.config = config

    def _parse_memory(self, value: str) -> int:
        """Parse memory string like '339M', '1024K', '2G', '0B' to bytes."""
        value = value.strip().rstrip("+-")  # Remove +/- indicators
        if not value or value == "0":
            return 0

        match = re.match(r"(\d+(?:\.\d+)?)\s*([BKMG])?", value, re.IGNORECASE)
        if not match:
            return 0

        num = float(match.group(1))
        suffix = (match.group(2) or "B").upper()

        multipliers = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
        return int(num * multipliers.get(suffix, 1))

    def _parse_top_output(self, raw: str) -> list[dict]:
        """Parse top text output into raw process dicts."""
        lines = raw.strip().split("\n")
        processes = []

        # Find header line
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("PID"):
                header_idx = i
                break

        if header_idx is None:
            return []

        # Parse data lines after header
        for line in lines[header_idx + 1 :]:
            parts = line.split()
            if len(parts) < 10:
                continue

            try:
                processes.append(
                    {
                        "pid": int(parts[0]),
                        "command": parts[1],
                        "cpu": float(parts[2]),
                        "state": parts[3],
                        "mem": self._parse_memory(parts[4]),
                        "cmprs": self._parse_memory(parts[5]),
                        "threads": int(parts[6].split("/")[0]),  # Handle "870/16" format
                        "csw": int(parts[7].rstrip("+")),
                        "sysbsd": int(parts[8].rstrip("+")),
                        "pageins": int(parts[9]),
                    }
                )
            except (ValueError, IndexError):
                continue

        return processes

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
        score = min(100, int(base_score * state_mult))

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
        )

    async def collect(self) -> ProcessSamples:
        """Run top, parse output, select rogues, compute scores."""
        start = time.monotonic()

        raw = await self._run_top()
        all_processes = self._parse_top_output(raw)
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

    async def _run_top(self) -> str:
        """Run top command and return output."""
        cmd = [
            "top",
            "-l",
            "2",  # 2 samples (need delta for accurate CPU %)
            "-s",
            "1",  # 1 second interval
            "-stats",
            "pid,command,cpu,state,mem,cmprs,threads,csw,sysbsd,pageins",
        ]

        log.debug("top_started", cmd=cmd)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # No timeout - if the system is paused, top takes longer.
        # That's exactly what we're measuring via elapsed_ms.
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")
            log.warning("top_failed", returncode=proc.returncode, stderr=stderr_text)
            raise RuntimeError(f"top failed: {stderr_text}")

        log.debug("top_completed", output_bytes=len(stdout))
        return stdout.decode(errors="replace")
