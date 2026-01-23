"""Tests for metrics collector."""

import plistlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.collector import (
    PowermetricsStream,
    StreamStatus,
    get_core_count,
    parse_powermetrics_sample,
)

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


@pytest.mark.asyncio
async def test_powermetrics_stream_status_not_started():
    """Stream status is NOT_STARTED before starting."""
    stream = PowermetricsStream(interval_ms=1000)
    assert stream.status == StreamStatus.NOT_STARTED


@pytest.mark.asyncio
async def test_powermetrics_stream_status_running():
    """Stream status is RUNNING after start."""
    stream = PowermetricsStream(interval_ms=1000)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stdout.__aiter__ = AsyncMock(return_value=iter([]))
        mock_process.terminate = MagicMock()
        mock_exec.return_value = mock_process

        await stream.start()
        assert stream.status == StreamStatus.RUNNING

        await stream.stop()


@pytest.mark.asyncio
async def test_powermetrics_stream_stop():
    """Stream can be stopped cleanly."""
    stream = PowermetricsStream(interval_ms=1000)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        mock_exec.return_value = mock_process

        await stream.start()
        await stream.stop()

        mock_process.terminate.assert_called_once()
        assert stream.status == StreamStatus.STOPPED


def test_get_core_count():
    """get_core_count returns positive integer."""
    count = get_core_count()
    assert count > 0


def test_powermetrics_result_matches_data_dictionary():
    """PowermetricsResult has exactly the fields from Data Dictionary."""
    from pause_monitor.collector import PowermetricsResult

    result = PowermetricsResult(
        elapsed_ns=100_000_000,
        throttled=False,
        cpu_power=5.2,
        gpu_pct=4.0,
        gpu_power=1.3,
        io_read_per_s=1024.0,
        io_write_per_s=512.0,
        wakeups_per_s=150.0,
        pageins_per_s=0.0,  # Critical for pause detection
        top_cpu_processes=[{"name": "test", "pid": 123, "cpu_ms_per_s": 100.0}],
        top_pagein_processes=[],  # No swap activity in this test
    )
    assert result.elapsed_ns == 100_000_000
    assert result.wakeups_per_s == 150.0
    assert result.pageins_per_s == 0.0

    # Verify removed fields don't exist
    assert not hasattr(result, "cpu_pct")
    assert not hasattr(result, "cpu_freq")
    assert not hasattr(result, "cpu_temp")
    assert not hasattr(result, "top_processes")  # Renamed to top_cpu_processes
