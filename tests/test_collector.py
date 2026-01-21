"""Tests for metrics collector."""

import plistlib
from datetime import datetime

from pause_monitor.collector import parse_powermetrics_sample

SAMPLE_PLIST = {
    "timestamp": datetime.now(),
    "processor": {
        "cpu_power": 5.5,
        "combined_power": 10.0,
        "package_idle_residency": 45.0,
        "clusters": [
            {
                "name": "E-Cluster",
                "cpus": [
                    {"cpu": 0, "freq_hz": 972_000_000, "idle_percent": 90.0},
                    {"cpu": 1, "freq_hz": 972_000_000, "idle_percent": 85.0},
                ],
            },
            {
                "name": "P-Cluster",
                "cpus": [
                    {"cpu": 4, "freq_hz": 3_500_000_000, "idle_percent": 50.0},
                    {"cpu": 5, "freq_hz": 3_500_000_000, "idle_percent": 55.0},
                ],
            },
        ],
    },
    "gpu": {"freq_hz": 1_398_000_000, "busy_percent": 25.0, "gpu_power": 2.5},
    "thermal_pressure": "Nominal",
    "is_charging": True,
}


def test_parse_powermetrics_cpu_usage():
    """Parser extracts CPU usage from idle percentages."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # Average of (100-90 + 100-85 + 100-50 + 100-55) / 4 = 30%
    assert 29.0 <= result.cpu_pct <= 31.0


def test_parse_powermetrics_cpu_freq():
    """Parser extracts max CPU frequency."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.cpu_freq == 3500  # MHz


def test_parse_powermetrics_gpu():
    """Parser extracts GPU busy percentage."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.gpu_pct == 25.0


def test_parse_powermetrics_throttled_nominal():
    """Nominal thermal pressure means not throttled."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.throttled is False


def test_parse_powermetrics_throttled_critical():
    """Critical thermal pressure means throttled."""
    plist = SAMPLE_PLIST.copy()
    plist["thermal_pressure"] = "Heavy"
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.throttled is True
