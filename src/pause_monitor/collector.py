"""Metrics collector using powermetrics."""

import asyncio
import os
import plistlib
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    pass

log = structlog.get_logger()


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

    # Disk I/O (from disk dict) — kept separate per Data Dictionary
    io_read_per_s: float  # bytes/sec from disk.rbytes_per_s
    io_write_per_s: float  # bytes/sec from disk.wbytes_per_s

    # Wakeups (summed from tasks array)
    wakeups_per_s: float  # Sum of tasks[].idle_wakeups_per_s

    # Page-ins (summed from tasks array) — CRITICAL for pause detection
    pageins_per_s: float  # Sum of tasks[].pageins_per_s

    # Top processes for culprit identification (two lists, 5 each)
    top_cpu_processes: list[dict]  # [{name, pid, cpu_ms_per_s}] — top 5 by CPU
    top_pagein_processes: list[dict]  # [{name, pid, pageins_per_s}] — top 5 by pageins


def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.

    Args:
        data: Raw plist bytes from powermetrics output

    Returns:
        PowermetricsResult with extracted metrics

    Note:
        Actual field extraction to be implemented in Task 1.2.
        Currently returns stub values for new fields.
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException:
        log.warning("invalid_plist_data")
        return PowermetricsResult(
            elapsed_ns=0,
            throttled=False,
            cpu_power=None,
            gpu_pct=None,
            gpu_power=None,
            io_read_per_s=0.0,
            io_write_per_s=0.0,
            wakeups_per_s=0.0,
            pageins_per_s=0.0,
            top_cpu_processes=[],
            top_pagein_processes=[],
        )

    # Thermal throttling
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure in ("Moderate", "Heavy", "Critical", "Sleeping")

    # GPU usage
    gpu_data = plist.get("gpu", {})
    gpu_pct = gpu_data.get("busy_percent")

    # TODO(stub): Task 1.2 will implement full field extraction
    # For now, return stub values for new fields
    return PowermetricsResult(
        elapsed_ns=0,  # Task 1.2: extract from plist
        throttled=throttled,
        cpu_power=None,  # Task 1.2: extract from processor.cpu_power
        gpu_pct=gpu_pct,
        gpu_power=None,  # Task 1.2: extract from processor.gpu_power
        io_read_per_s=0.0,  # Task 1.2: extract from disk.rbytes_per_s
        io_write_per_s=0.0,  # Task 1.2: extract from disk.wbytes_per_s
        wakeups_per_s=0.0,  # Task 1.2: sum from tasks[].idle_wakeups_per_s
        pageins_per_s=0.0,  # Task 1.2: sum from tasks[].pageins_per_s
        top_cpu_processes=[],  # Task 1.2: top 5 by CPU
        top_pagein_processes=[],  # Task 1.2: top 5 by pageins
    )


class PowermetricsStream:
    """Async stream of powermetrics data.

    Uses streaming plist output for lower latency than exec-per-sample.
    """

    POWERMETRICS_CMD = [
        "/usr/bin/powermetrics",
        "--samplers",
        "cpu_power,gpu_power,thermal",
        "-f",
        "plist",
    ]

    def __init__(self, interval_ms: int = 1000):
        self.interval_ms = interval_ms
        self._process: Process | None = None
        self._status = StreamStatus.NOT_STARTED
        self._buffer = b""

    @property
    def status(self) -> StreamStatus:
        """Current stream status."""
        return self._status

    async def start(self) -> None:
        """Start the powermetrics subprocess."""
        if self._process is not None:
            return

        cmd = self.POWERMETRICS_CMD + ["-i", str(self.interval_ms)]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Give it a moment to fail if it's going to
            await asyncio.sleep(0.1)

            if self._process.returncode is not None:
                # Process already exited - likely permission error
                stderr = b""
                if self._process.stderr:
                    stderr = await self._process.stderr.read()
                stderr_msg = stderr.decode().strip()

                self._status = StreamStatus.FAILED
                if "superuser" in stderr_msg.lower():
                    log.error("powermetrics_requires_sudo")
                    raise PermissionError(
                        "powermetrics requires root privileges. Run with: sudo pause-monitor daemon"
                    )
                else:
                    log.error("powermetrics_start_failed", error=stderr_msg)
                    raise RuntimeError(f"powermetrics failed: {stderr_msg}")

            self._status = StreamStatus.RUNNING
            log.info("powermetrics_started", interval_ms=self.interval_ms)
        except (FileNotFoundError, PermissionError) as e:
            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=str(e))
            raise

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
