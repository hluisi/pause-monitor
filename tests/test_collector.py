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
    TopCollector,
    get_core_count,
    parse_powermetrics_sample,
)
from pause_monitor.config import Config

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


# TopCollector tests

SAMPLE_TOP_OUTPUT = """
Processes: 500 total, 3 running, 497 sleeping, 4000 threads
2026/01/23 12:00:00
Load Avg: 2.00, 1.50, 1.00

PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
7229   chrome           47.1 running  339M   10M    38     1134810    3961273    68
409    WindowServer     27.7 running  1473M  0B     26     84562346   103373638  3427
0      kernel_task      18.1 stuck    43M    0B     870    793476910  0          0
620    zombie_proc      0.0  zombie   0B     0B     0      0          0          0
"""


def test_parse_top_output():
    """Should parse top output into process dicts."""
    collector = TopCollector(Config())
    processes = collector._parse_top_output(SAMPLE_TOP_OUTPUT)

    assert len(processes) == 4

    chrome = next(p for p in processes if p["command"] == "chrome")
    assert chrome["pid"] == 7229
    assert chrome["cpu"] == 47.1
    assert chrome["state"] == "running"
    assert chrome["mem"] == 339 * 1024 * 1024  # 339M in bytes
    assert chrome["pageins"] == 68


def test_parse_memory_suffixes():
    """Should handle M, K, G, B suffixes."""
    collector = TopCollector(Config())
    assert collector._parse_memory("339M") == 339 * 1024 * 1024
    assert collector._parse_memory("1473M") == 1473 * 1024 * 1024
    assert collector._parse_memory("43M") == 43 * 1024 * 1024
    assert collector._parse_memory("0B") == 0
    assert collector._parse_memory("1024K") == 1024 * 1024
    assert collector._parse_memory("2G") == 2 * 1024 * 1024 * 1024


# Rogue selection tests


def test_select_rogues_stuck_always_included():
    """Stuck processes should always be included."""
    config = Config()
    collector = TopCollector(config)

    processes = [
        {
            "pid": 1,
            "command": "normal",
            "cpu": 1.0,
            "state": "sleeping",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
        {
            "pid": 2,
            "command": "stuck_proc",
            "cpu": 0.0,
            "state": "stuck",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
    ]

    rogues = collector._select_rogues(processes)

    stuck = [r for r in rogues if r["command"] == "stuck_proc"]
    assert len(stuck) == 1
    assert "stuck" in stuck[0]["_categories"]


def test_select_rogues_top_n_per_category():
    """Should select top N per category."""
    config = Config()
    config.rogue_selection.cpu.count = 2
    collector = TopCollector(config)

    processes = [
        {
            "pid": 1,
            "command": "high_cpu",
            "cpu": 90.0,
            "state": "running",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
        {
            "pid": 2,
            "command": "med_cpu",
            "cpu": 50.0,
            "state": "running",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
        {
            "pid": 3,
            "command": "low_cpu",
            "cpu": 10.0,
            "state": "running",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
    ]

    rogues = collector._select_rogues(processes)

    # Should have top 2 by CPU
    cpu_rogues = [r for r in rogues if "cpu" in r["_categories"]]
    commands = {r["command"] for r in cpu_rogues}
    assert "high_cpu" in commands
    assert "med_cpu" in commands
    assert "low_cpu" not in commands


def test_select_rogues_deduplicates():
    """Process in multiple categories should appear once."""
    config = Config()
    collector = TopCollector(config)

    processes = [
        {
            "pid": 1,
            "command": "multi",
            "cpu": 90.0,
            "state": "running",
            "mem": 1000000000,
            "cmprs": 0,
            "pageins": 100,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
    ]

    rogues = collector._select_rogues(processes)

    assert len(rogues) == 1
    assert "cpu" in rogues[0]["_categories"]
    assert "mem" in rogues[0]["_categories"]
    assert "pageins" in rogues[0]["_categories"]


def test_select_rogues_zombie_state_included():
    """Zombie processes should be included via state selection."""
    config = Config()
    # Ensure state selection is enabled with zombie
    config.rogue_selection.state.enabled = True
    config.rogue_selection.state.states = ["zombie"]
    collector = TopCollector(config)

    processes = [
        {
            "pid": 1,
            "command": "normal",
            "cpu": 1.0,
            "state": "sleeping",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
        {
            "pid": 2,
            "command": "zombie_proc",
            "cpu": 0.0,
            "state": "zombie",
            "mem": 0,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 0,
        },
    ]

    rogues = collector._select_rogues(processes)

    zombie = [r for r in rogues if r["command"] == "zombie_proc"]
    assert len(zombie) == 1
    assert "state" in zombie[0]["_categories"]


def test_select_rogues_threshold_filters():
    """Processes below threshold should not be selected."""
    config = Config()
    config.rogue_selection.cpu.enabled = True
    config.rogue_selection.cpu.count = 5
    config.rogue_selection.cpu.threshold = 50.0  # Only include CPU > 50%
    # Disable other categories to isolate test
    config.rogue_selection.mem.enabled = False
    config.rogue_selection.cmprs.enabled = False
    config.rogue_selection.threads.enabled = False
    config.rogue_selection.csw.enabled = False
    config.rogue_selection.sysbsd.enabled = False
    config.rogue_selection.pageins.enabled = False
    config.rogue_selection.state.enabled = False
    collector = TopCollector(config)

    processes = [
        {
            "pid": 1,
            "command": "high_cpu",
            "cpu": 80.0,
            "state": "running",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
        {
            "pid": 2,
            "command": "low_cpu",
            "cpu": 30.0,
            "state": "running",
            "mem": 100,
            "cmprs": 0,
            "pageins": 0,
            "csw": 0,
            "sysbsd": 0,
            "threads": 1,
        },
    ]

    rogues = collector._select_rogues(processes)

    # Only high_cpu should be selected (above threshold)
    assert len(rogues) == 1
    assert rogues[0]["command"] == "high_cpu"
    assert "cpu" in rogues[0]["_categories"]


# Process scoring tests


def test_score_process_cpu_heavy():
    """High CPU should result in high score."""
    config = Config()
    collector = TopCollector(config)

    proc = {
        "pid": 1,
        "command": "cpu_hog",
        "cpu": 100.0,
        "state": "running",
        "mem": 0,
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "_categories": {"cpu"},
    }

    scored = collector._score_process(proc)

    assert scored.score >= 20  # CPU weight is 25, 100% CPU gives exactly 25


def test_score_process_stuck():
    """Stuck state should add significant score."""
    config = Config()
    collector = TopCollector(config)

    proc = {
        "pid": 1,
        "command": "stuck",
        "cpu": 0.0,
        "state": "stuck",
        "mem": 0,
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "_categories": {"stuck"},
    }

    scored = collector._score_process(proc)

    assert scored.score >= 15  # State weight is 20, stuck gives exactly 20


def test_score_process_categories_preserved():
    """Categories should be preserved in ProcessScore."""
    config = Config()
    collector = TopCollector(config)

    proc = {
        "pid": 1,
        "command": "test",
        "cpu": 50.0,
        "state": "running",
        "mem": 1000000000,
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "_categories": {"cpu", "mem"},
    }

    scored = collector._score_process(proc)

    assert scored.categories == frozenset({"cpu", "mem"})


def test_score_process_all_factors():
    """All factors should contribute to score."""
    config = Config()
    collector = TopCollector(config)

    # High values for all factors
    proc = {
        "pid": 1,
        "command": "stress_all",
        "cpu": 100.0,
        "state": "stuck",
        "mem": 8 * 1024**3,  # 8GB (max normalization)
        "cmprs": 1 * 1024**3,  # 1GB (max normalization)
        "pageins": 1000,  # max normalization
        "csw": 100000,  # max normalization
        "sysbsd": 100000,  # max normalization
        "threads": 1000,  # max normalization
        "_categories": {"cpu", "stuck", "mem"},
    }

    scored = collector._score_process(proc)

    # With all factors maxed, should be at or near 100
    assert scored.score >= 90


def test_score_process_returns_process_score():
    """Should return a ProcessScore dataclass with correct fields."""
    config = Config()
    collector = TopCollector(config)

    proc = {
        "pid": 42,
        "command": "myproc",
        "cpu": 25.0,
        "state": "sleeping",
        "mem": 1024**2,  # 1MB
        "cmprs": 512,
        "pageins": 5,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
        "_categories": {"cpu"},
    }

    scored = collector._score_process(proc)

    assert isinstance(scored, ProcessScore)
    assert scored.pid == 42
    assert scored.command == "myproc"
    assert scored.cpu == 25.0
    assert scored.state == "sleeping"
    assert scored.mem == 1024**2
    assert scored.cmprs == 512
    assert scored.pageins == 5
    assert scored.csw == 100
    assert scored.sysbsd == 50
    assert scored.threads == 4
    assert isinstance(scored.score, int)
    assert 0 <= scored.score <= 100


def test_normalize_state_values():
    """State normalization should assign correct weights."""
    config = Config()
    collector = TopCollector(config)

    # Test all state values
    assert collector._normalize_state("stuck") == 1.0
    assert collector._normalize_state("zombie") == 0.8
    assert collector._normalize_state("halted") == 0.6
    assert collector._normalize_state("stopped") == 0.4
    assert collector._normalize_state("running") == 0.0
    assert collector._normalize_state("sleeping") == 0.0
    assert collector._normalize_state("unknown") == 0.0
