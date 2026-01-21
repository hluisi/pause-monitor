"""Metrics collector using powermetrics."""

import plistlib
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger()


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
