"""Metrics collector using powermetrics."""

import asyncio
import plistlib
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger()


class StreamStatus(Enum):
    """Powermetrics stream status."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class PowermetricsResult:
    """Parsed powermetrics sample data."""

    cpu_pct: float | None
    cpu_freq: int | None  # MHz
    cpu_temp: float | None
    throttled: bool | None
    gpu_pct: float | None


def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.

    Args:
        data: Raw plist bytes from powermetrics output

    Returns:
        PowermetricsResult with extracted metrics
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException:
        log.warning("invalid_plist_data")
        return PowermetricsResult(
            cpu_pct=None,
            cpu_freq=None,
            cpu_temp=None,
            throttled=None,
            gpu_pct=None,
        )

    # Extract CPU usage from cluster data
    cpu_pct = _extract_cpu_usage(plist.get("processor", {}))

    # Extract max CPU frequency
    cpu_freq = _extract_cpu_freq(plist.get("processor", {}))

    # CPU temperature (not always available)
    cpu_temp = None
    if "processor" in plist and "cpu_thermal_level" in plist["processor"]:
        cpu_temp = plist["processor"]["cpu_thermal_level"]

    # Thermal throttling
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure in ("Moderate", "Heavy", "Critical", "Sleeping")

    # GPU usage
    gpu_data = plist.get("gpu", {})
    gpu_pct = gpu_data.get("busy_percent")

    return PowermetricsResult(
        cpu_pct=cpu_pct,
        cpu_freq=cpu_freq,
        cpu_temp=cpu_temp,
        throttled=throttled,
        gpu_pct=gpu_pct,
    )


def _extract_cpu_usage(processor: dict[str, Any]) -> float | None:
    """Extract CPU usage percentage from processor data."""
    clusters = processor.get("clusters", [])
    if not clusters:
        return None

    total_usage = 0.0
    cpu_count = 0

    for cluster in clusters:
        for cpu in cluster.get("cpus", []):
            idle_pct = cpu.get("idle_percent", 100.0)
            total_usage += 100.0 - idle_pct
            cpu_count += 1

    return total_usage / cpu_count if cpu_count > 0 else None


def _extract_cpu_freq(processor: dict[str, Any]) -> int | None:
    """Extract maximum CPU frequency in MHz."""
    clusters = processor.get("clusters", [])
    if not clusters:
        return None

    max_freq_hz = 0
    for cluster in clusters:
        for cpu in cluster.get("cpus", []):
            freq_hz = cpu.get("freq_hz", 0)
            max_freq_hz = max(max_freq_hz, freq_hz)

    return max_freq_hz // 1_000_000 if max_freq_hz > 0 else None


class PowermetricsStream:
    """Async stream of powermetrics data.

    Uses streaming plist output for lower latency than exec-per-sample.
    """

    POWERMETRICS_CMD = [
        "/usr/bin/powermetrics",
        "--samplers",
        "cpu_power,gpu_power,thermal",
        "--output-format",
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
                stderr=asyncio.subprocess.DEVNULL,
            )
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
