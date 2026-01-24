"""Metrics collector using powermetrics."""

import asyncio
import json
import os
import plistlib
import re
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from pause_monitor.config import Config

if TYPE_CHECKING:
    pass

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


class StreamStatus(Enum):
    """Powermetrics stream status."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class PowermetricsResult:
    """Parsed powermetrics sample data.

    All fields derived from powermetrics plist output.
    See Data Dictionary for field mappings and rationale.
    """

    # Timing (for pause detection)
    elapsed_ns: int  # Actual sample interval from powermetrics

    # Thermal
    throttled: bool  # True if thermal_pressure != "Nominal"

    # CPU power (from processor dict)
    cpu_power: float | None  # Milliwatts from processor.cpu_power

    # GPU (from gpu dict)
    gpu_pct: float | None  # (1 - idle_ratio) * 100
    gpu_power: float | None  # Milliwatts from processor.gpu_power

    # Disk I/O (from disk dict) — system-wide aggregates
    io_read_per_s: float  # bytes/sec from disk.rbytes_per_s
    io_write_per_s: float  # bytes/sec from disk.wbytes_per_s

    # Wakeups (summed from tasks array)
    wakeups_per_s: float  # Sum of tasks[].idle_wakeups_per_s

    # Page-ins (summed from tasks array) — CRITICAL for pause detection
    pageins_per_s: float  # Sum of tasks[].pageins_per_s

    # Top 5 processes for culprit identification
    top_cpu_processes: list[dict]  # [{name, pid, cpu_ms_per_s}]
    top_pagein_processes: list[dict]  # [{name, pid, pageins_per_s}]
    top_wakeup_processes: list[dict]  # [{name, pid, wakeups_per_s}]
    top_diskio_processes: list[dict]  # [{name, pid, diskio_per_s}] read+write combined


def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.

    Extracts metrics per the Data Dictionary field mappings.

    Args:
        data: Raw plist bytes from powermetrics output

    Returns:
        PowermetricsResult with extracted metrics

    Raises:
        ValueError: If plist data is invalid
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException as e:
        raise ValueError(f"Invalid powermetrics plist data: {e}") from e

    # Timing
    elapsed_ns = plist.get("elapsed_ns", 0)

    # Thermal throttling: anything other than "Nominal" means throttled
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure != "Nominal"

    # CPU power from processor dict
    processor = plist.get("processor", {})
    cpu_power = processor.get("cpu_power")  # Milliwatts

    # GPU power is in processor dict, not gpu dict
    gpu_power = processor.get("gpu_power")  # Milliwatts

    # GPU busy % from gpu dict: busy = 1 - idle_ratio
    gpu_data = plist.get("gpu", {})
    idle_ratio = gpu_data.get("idle_ratio")
    gpu_pct = (1.0 - idle_ratio) * 100.0 if idle_ratio is not None else None

    # Disk I/O — keep read/write separate per Data Dictionary
    disk_data = plist.get("disk", {})
    io_read_per_s = disk_data.get("rbytes_per_s", 0.0)
    io_write_per_s = disk_data.get("wbytes_per_s", 0.0)

    # Tasks: sum wakeups and pageins, collect process info
    wakeups_per_s = 0.0
    pageins_per_s = 0.0
    all_processes: list[dict] = []

    for task in plist.get("tasks", []):
        task_wakeups = task.get("idle_wakeups_per_s", 0.0)
        wakeups_per_s += task_wakeups

        task_pageins = task.get("pageins_per_s", 0.0)
        pageins_per_s += task_pageins

        # Per-process disk I/O (read + write combined)
        task_diskio = task.get("diskio_bytesread_per_s", 0.0) + task.get(
            "diskio_byteswritten_per_s", 0.0
        )

        proc = {
            "name": task.get("name", "unknown"),
            "pid": task.get("pid", 0),
            "cpu_ms_per_s": task.get("cputime_ms_per_s", 0.0),
            "pageins_per_s": task_pageins,
            "wakeups_per_s": task_wakeups,
            "diskio_per_s": task_diskio,
        }
        all_processes.append(proc)

    # Top 5 by CPU usage
    top_cpu_processes = sorted(all_processes, key=lambda p: p["cpu_ms_per_s"], reverse=True)[:5]

    # Top 5 by pageins (only include processes with pageins > 0)
    top_pagein_processes = sorted(
        [p for p in all_processes if p["pageins_per_s"] > 0],
        key=lambda p: p["pageins_per_s"],
        reverse=True,
    )[:5]

    # Top 5 by wakeups (only include processes with wakeups > 0)
    top_wakeup_processes = sorted(
        [p for p in all_processes if p["wakeups_per_s"] > 0],
        key=lambda p: p["wakeups_per_s"],
        reverse=True,
    )[:5]

    # Top 5 by disk I/O (only include processes with diskio > 0)
    top_diskio_processes = sorted(
        [p for p in all_processes if p["diskio_per_s"] > 0],
        key=lambda p: p["diskio_per_s"],
        reverse=True,
    )[:5]

    return PowermetricsResult(
        elapsed_ns=elapsed_ns,
        throttled=throttled,
        cpu_power=cpu_power,
        gpu_pct=gpu_pct,
        gpu_power=gpu_power,
        io_read_per_s=io_read_per_s,
        io_write_per_s=io_write_per_s,
        wakeups_per_s=wakeups_per_s,
        pageins_per_s=pageins_per_s,
        top_cpu_processes=top_cpu_processes,
        top_pagein_processes=top_pagein_processes,
        top_wakeup_processes=top_wakeup_processes,
        top_diskio_processes=top_diskio_processes,
    )


class PowermetricsStream:
    """Async stream of powermetrics data.

    Uses streaming plist output for lower latency than exec-per-sample.
    """

    POWERMETRICS_CMD = [
        "/usr/bin/powermetrics",
        "--samplers",
        "cpu_power,gpu_power,thermal,tasks,disk",  # tasks for wakeups, disk for I/O
        "-f",
        "plist",
    ]

    def __init__(self, interval_ms: int = 100):  # 100ms for 10Hz sampling
        self.interval_ms = interval_ms
        self._process: Process | None = None
        self._status = StreamStatus.NOT_STARTED
        self._buffer = b""

    @property
    def status(self) -> StreamStatus:
        """Current stream status."""
        return self._status

    async def start(self) -> None:
        """Start the powermetrics subprocess.

        Raises:
            RuntimeError: If powermetrics fails to start (permission denied, not found, etc.)
        """
        if self._process is not None:
            return

        cmd = self.POWERMETRICS_CMD + ["-i", str(self.interval_ms)]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except PermissionError as e:
            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=str(e))
            raise RuntimeError(
                f"powermetrics failed to start: {e}. "
                "Daemon requires root privileges (sudo) to run powermetrics."
            ) from e
        except FileNotFoundError as e:
            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=str(e))
            raise RuntimeError(
                f"powermetrics not found: {e}. Ensure /usr/bin/powermetrics exists (macOS only)."
            ) from e
        except OSError as e:
            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=str(e))
            raise RuntimeError(f"powermetrics failed to start: {e}") from e

        # Give it a moment to fail if it's going to
        await asyncio.sleep(0.1)

        if self._process.returncode is not None:
            # Process already exited - likely permission error
            stderr = b""
            if self._process.stderr:
                stderr = await self._process.stderr.read()
            stderr_msg = stderr.decode().strip()

            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=stderr_msg)
            raise RuntimeError(f"powermetrics failed to start: {stderr_msg}")

        self._status = StreamStatus.RUNNING
        log.info("powermetrics_started", interval_ms=self.interval_ms)

    async def stop(self) -> None:
        """Stop the powermetrics subprocess."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            await self._process.wait()
        except ProcessLookupError:
            pass

        self._process = None
        self._status = StreamStatus.STOPPED
        log.info("powermetrics_stopped")

    def terminate(self) -> None:
        """Synchronously kill the subprocess (for signal handlers).

        Uses SIGKILL because powermetrics may not respond to SIGTERM quickly.
        """
        if self._process is None:
            return
        try:
            self._process.kill()  # SIGKILL - cannot be caught or ignored
        except ProcessLookupError:
            pass  # Already dead  # Already dead

    async def read_samples(self) -> AsyncIterator[PowermetricsResult]:
        """Yield parsed samples as they become available.

        powermetrics outputs plists separated by NUL bytes (\\0).
        """
        if self._process is None or self._process.stdout is None:
            return

        async for chunk in self._process.stdout:
            self._buffer += chunk

            # powermetrics separates plists with NUL bytes
            while b"\0" in self._buffer:
                plist_data, self._buffer = self._buffer.split(b"\0", 1)

                # Skip empty chunks
                if not plist_data.strip():
                    continue

                result = parse_powermetrics_sample(plist_data)
                yield result


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
