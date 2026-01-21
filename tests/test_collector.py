"""Tests for metrics collector."""

import plistlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.collector import (
    PowermetricsStream,
    StreamStatus,
    get_core_count,
    get_system_metrics,
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


def test_get_system_metrics_returns_complete():
    """get_system_metrics returns all required fields."""
    metrics = get_system_metrics()

    assert metrics.load_avg is not None
    assert metrics.mem_available is not None
    assert metrics.swap_used is not None
    assert metrics.io_read is not None
    assert metrics.io_write is not None


# Sample Policy Tests


def test_sample_policy_initial_state():
    """SamplePolicy starts in NORMAL state."""
    from pause_monitor.collector import SamplePolicy, SamplingState

    policy = SamplePolicy(normal_interval=5, elevated_interval=1)
    assert policy.state == SamplingState.NORMAL
    assert policy.current_interval == 5


def test_sample_policy_elevates_on_threshold():
    """SamplePolicy elevates when stress exceeds threshold."""
    from pause_monitor.collector import SamplePolicy, SamplingState
    from pause_monitor.stress import StressBreakdown

    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
    )

    stress = StressBreakdown(load=20, memory=15, thermal=0, latency=0, io=0)
    policy.update(stress)

    assert policy.state == SamplingState.ELEVATED
    assert policy.current_interval == 1


def test_sample_policy_returns_to_normal():
    """SamplePolicy returns to normal when stress drops below de-elevation threshold."""
    from pause_monitor.collector import SamplePolicy, SamplingState
    from pause_monitor.stress import StressBreakdown

    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        de_elevation_threshold=20,
        cooldown_samples=3,
    )

    # Elevate
    high_stress = StressBreakdown(load=40, memory=0, thermal=0, latency=0, io=0)
    policy.update(high_stress)
    assert policy.state == SamplingState.ELEVATED

    # Drop below de-elevation threshold for cooldown period
    low_stress = StressBreakdown(load=5, memory=0, thermal=0, latency=0, io=0)
    for _ in range(3):
        policy.update(low_stress)

    assert policy.state == SamplingState.NORMAL


def test_sample_policy_hysteresis():
    """SamplePolicy stays elevated when stress is between thresholds (hysteresis)."""
    from pause_monitor.collector import SamplePolicy, SamplingState
    from pause_monitor.stress import StressBreakdown

    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        de_elevation_threshold=20,
        cooldown_samples=3,
    )

    # Elevate
    high_stress = StressBreakdown(load=40, memory=0, thermal=0, latency=0, io=0)
    policy.update(high_stress)
    assert policy.state == SamplingState.ELEVATED

    # Drop to 25 (between 20 and 30) - should stay elevated
    mid_stress = StressBreakdown(load=25, memory=0, thermal=0, latency=0, io=0)
    for _ in range(10):  # Even many samples shouldn't trigger de-elevation
        policy.update(mid_stress)

    assert policy.state == SamplingState.ELEVATED  # Still elevated due to hysteresis


def test_sample_policy_critical_triggers_snapshot():
    """SamplePolicy triggers snapshot on critical stress."""
    from pause_monitor.collector import SamplePolicy
    from pause_monitor.stress import StressBreakdown

    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        critical_threshold=60,
    )

    stress = StressBreakdown(load=40, memory=20, thermal=20, latency=0, io=0)
    result = policy.update(stress)

    assert result.should_snapshot is True
