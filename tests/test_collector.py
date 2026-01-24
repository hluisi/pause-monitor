"""Tests for metrics collector."""

import plistlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.collector import (
    PowermetricsStream,
    ProcessSamples,
    ProcessScore,
    StreamStatus,
    get_core_count,
    parse_powermetrics_sample,
)

SAMPLE_PLIST = {
    "timestamp": datetime.now(),
    "elapsed_ns": 1_000_000_000,
    "processor": {
        "cpu_power": 5.5,
        "gpu_power": 2.5,
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
    "gpu": {"freq_hz": 1_398_000_000, "idle_ratio": 0.75},  # 25% busy
    "disk": {"rbytes_per_s": 1024.0, "wbytes_per_s": 512.0},
    "thermal_pressure": "Nominal",
    "is_charging": True,
    "tasks": [
        {
            "name": "process_a",
            "pid": 100,
            "cputime_ms_per_s": 500.0,
            "idle_wakeups_per_s": 50.0,
            "pageins_per_s": 10.0,
        },
        {
            "name": "process_b",
            "pid": 200,
            "cputime_ms_per_s": 300.0,
            "idle_wakeups_per_s": 100.0,
            "pageins_per_s": 0.0,
        },
        {
            "name": "process_c",
            "pid": 300,
            "cputime_ms_per_s": 100.0,
            "idle_wakeups_per_s": 25.0,
            "pageins_per_s": 5.0,
        },
    ],
}


def test_parse_powermetrics_gpu():
    """Parser extracts GPU busy percentage from idle_ratio."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # gpu_pct = (1 - idle_ratio) * 100 = (1 - 0.75) * 100 = 25.0
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
        top_wakeup_processes=[],
        top_diskio_processes=[],
    )
    assert result.elapsed_ns == 100_000_000
    assert result.wakeups_per_s == 150.0
    assert result.pageins_per_s == 0.0

    # Verify removed fields don't exist
    assert not hasattr(result, "cpu_pct")
    assert not hasattr(result, "cpu_freq")
    assert not hasattr(result, "cpu_temp")
    assert not hasattr(result, "top_processes")  # Renamed to top_cpu_processes


def test_parse_invalid_plist_raises():
    """Invalid plist data raises ValueError, not silent fake data."""
    with pytest.raises(ValueError, match="Invalid powermetrics plist"):
        parse_powermetrics_sample(b"not valid plist data")


def test_parse_elapsed_ns():
    """Parser extracts elapsed_ns from plist."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.elapsed_ns == 1_000_000_000


def test_parse_cpu_power():
    """Parser extracts cpu_power from processor dict."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.cpu_power == 5.5


def test_parse_gpu_power():
    """Parser extracts gpu_power from processor dict (not gpu dict)."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.gpu_power == 2.5


def test_parse_wakeups_from_idle_wakeups_per_s():
    """Wakeups are summed from tasks[].idle_wakeups_per_s."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # 50.0 + 100.0 + 25.0 = 175.0
    assert result.wakeups_per_s == 175.0


def test_parse_io_kept_separate():
    """IO read and write are kept separate per Data Dictionary."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.io_read_per_s == 1024.0
    assert result.io_write_per_s == 512.0


def test_parse_gpu_from_idle_ratio():
    """GPU percentage calculated from (1 - idle_ratio) * 100."""
    plist = SAMPLE_PLIST.copy()
    plist["gpu"] = {"idle_ratio": 0.6}  # 40% busy
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.gpu_pct == pytest.approx(40.0)


def test_parse_gpu_none_when_no_idle_ratio():
    """GPU percentage is None when idle_ratio is missing."""
    plist = SAMPLE_PLIST.copy()
    plist["gpu"] = {}  # No idle_ratio
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.gpu_pct is None


def test_parse_pageins_summed_across_tasks():
    """Pageins are summed from tasks[].pageins_per_s."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # 10.0 + 0.0 + 5.0 = 15.0
    assert result.pageins_per_s == 15.0


def test_parse_top_cpu_processes_sorted():
    """Top CPU processes sorted by cputime_ms_per_s descending."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert len(result.top_cpu_processes) == 3  # Only 3 processes in sample
    # Sorted by cpu_ms_per_s: process_a (500), process_b (300), process_c (100)
    assert result.top_cpu_processes[0]["name"] == "process_a"
    assert result.top_cpu_processes[0]["cpu_ms_per_s"] == 500.0
    assert result.top_cpu_processes[1]["name"] == "process_b"
    assert result.top_cpu_processes[2]["name"] == "process_c"


def test_parse_top_pagein_processes_sorted():
    """Top pagein processes sorted by pageins_per_s descending, excluding zeros."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # process_b has pageins_per_s=0, so excluded
    assert len(result.top_pagein_processes) == 2
    # Sorted: process_a (10.0), process_c (5.0)
    assert result.top_pagein_processes[0]["name"] == "process_a"
    assert result.top_pagein_processes[0]["pageins_per_s"] == 10.0
    assert result.top_pagein_processes[1]["name"] == "process_c"


def test_parse_top_processes_limited_to_5():
    """Top process lists limited to 5 entries."""
    plist = SAMPLE_PLIST.copy()
    plist["tasks"] = [
        {
            "name": f"proc_{i}",
            "pid": i,
            "cputime_ms_per_s": float(100 - i),
            "idle_wakeups_per_s": 0.0,
            "pageins_per_s": float(i + 1),
        }
        for i in range(10)
    ]
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert len(result.top_cpu_processes) == 5
    assert len(result.top_pagein_processes) == 5


def test_parse_throttled_moderate():
    """Moderate thermal pressure means throttled."""
    plist = SAMPLE_PLIST.copy()
    plist["thermal_pressure"] = "Moderate"
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.throttled is True


def test_parse_throttled_sleeping():
    """Sleeping thermal pressure means throttled."""
    plist = SAMPLE_PLIST.copy()
    plist["thermal_pressure"] = "Sleeping"
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.throttled is True


def test_powermetrics_stream_default_interval_is_100ms():
    """PowermetricsStream should default to 100ms for 10Hz sampling."""
    stream = PowermetricsStream()
    assert stream.interval_ms == 100


def test_powermetrics_stream_includes_tasks_and_disk_samplers():
    """PowermetricsStream should include tasks and disk samplers."""
    stream = PowermetricsStream()
    samplers_arg = stream.POWERMETRICS_CMD[stream.POWERMETRICS_CMD.index("--samplers") + 1]
    assert "tasks" in samplers_arg
    assert "disk" in samplers_arg


@pytest.mark.asyncio
async def test_powermetrics_stream_raises_on_permission_denied(monkeypatch):
    """PowermetricsStream.start() should raise RuntimeError if permission denied."""
    import asyncio

    async def mock_create_subprocess(*args, **kwargs):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess)

    stream = PowermetricsStream()
    with pytest.raises(RuntimeError, match="powermetrics failed to start"):
        await stream.start()


@pytest.mark.asyncio
async def test_powermetrics_stream_raises_on_not_found(monkeypatch):
    """PowermetricsStream.start() should raise RuntimeError if not found."""
    import asyncio

    async def mock_create_subprocess(*args, **kwargs):
        raise FileNotFoundError("powermetrics not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess)

    stream = PowermetricsStream()
    with pytest.raises(RuntimeError, match="powermetrics not found"):
        await stream.start()


# ProcessScore and ProcessSamples tests


def test_process_score_to_dict():
    """ProcessScore should serialize to dict."""
    ps = ProcessScore(
        pid=123,
        command="test",
        cpu=50.0,
        state="running",
        mem=1000000,
        cmprs=0,
        pageins=10,
        csw=100,
        sysbsd=50,
        threads=4,
        score=42,
        categories=frozenset({"cpu", "pageins"}),
    )
    d = ps.to_dict()
    assert d["pid"] == 123
    assert d["score"] == 42
    assert set(d["categories"]) == {"cpu", "pageins"}


def test_process_score_from_dict():
    """ProcessScore should deserialize from dict."""
    d = {
        "pid": 123,
        "command": "test",
        "cpu": 50.0,
        "state": "running",
        "mem": 1000000,
        "cmprs": 0,
        "pageins": 10,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
        "score": 42,
        "categories": ["cpu", "pageins"],
    }
    ps = ProcessScore.from_dict(d)
    assert ps.pid == 123
    assert ps.categories == frozenset({"cpu", "pageins"})


def test_process_samples_json_roundtrip():
    """ProcessSamples should roundtrip through JSON."""
    samples = ProcessSamples(
        timestamp=datetime(2026, 1, 23, 12, 0, 0),
        elapsed_ms=1050,
        process_count=500,
        max_score=75,
        rogues=[
            ProcessScore(
                pid=1,
                command="test",
                cpu=80.0,
                state="running",
                mem=1000,
                cmprs=0,
                pageins=0,
                csw=10,
                sysbsd=5,
                threads=2,
                score=75,
                categories=frozenset({"cpu"}),
            ),
        ],
    )
    json_str = samples.to_json()
    restored = ProcessSamples.from_json(json_str)
    assert restored.max_score == 75
    assert len(restored.rogues) == 1
    assert restored.rogues[0].command == "test"
