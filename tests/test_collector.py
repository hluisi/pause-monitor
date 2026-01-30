"""Tests for metrics collector."""

from datetime import datetime

import pytest

from pause_monitor.collector import (
    ProcessSamples,
    ProcessScore,
    TopCollector,
    get_core_count,
)
from pause_monitor.config import Config


def test_get_core_count():
    """get_core_count returns positive integer."""
    count = get_core_count()
    assert count > 0


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
        captured_at=1706000000.0,
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
        "captured_at": 1706000000.0,
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
                captured_at=1706000000.0,
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


def test_parse_top_output_uses_second_sample():
    """Should use sample 2 (accurate delta CPU%) not sample 1 (instantaneous).

    top -l 2 outputs two samples. Sample 1 has inaccurate instantaneous CPU%.
    Sample 2 has accurate delta CPU% over the 1-second interval.
    The parser should use the LAST header to get sample 2's data.
    """
    two_sample_output = """
Processes: 500 total, 3 running, 497 sleeping
Load Avg: 2.00, 1.50, 1.00

PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
7229   chrome           95.0 running  339M   10M    38     1000       2000       50
409    WindowServer     80.0 running  1473M  0B     26     3000       4000       100

Processes: 500 total, 2 running, 498 sleeping
Load Avg: 2.10, 1.55, 1.05

PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
7229   chrome           4.2  running  340M   11M    38     1100       2100       55
409    WindowServer     2.8  running  1475M  0B     26     3100       4100       105
"""
    collector = TopCollector(Config())
    processes = collector._parse_top_output(two_sample_output)

    # Should only have 2 processes (sample 2), not 4 (both samples)
    assert len(processes) == 2

    # Should have sample 2's accurate CPU%, not sample 1's inflated values
    chrome = next(p for p in processes if p["command"] == "chrome")
    assert chrome["cpu"] == 4.2  # NOT 95.0 from sample 1

    windowserver = next(p for p in processes if p["command"] == "WindowServer")
    assert windowserver["cpu"] == 2.8  # NOT 80.0 from sample 1

    # Other metrics should also be from sample 2
    assert chrome["mem"] == 340 * 1024 * 1024  # 340M, not 339M
    assert chrome["pageins"] == 55  # not 50


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

    # Stuck state with 1.0 multiplier should contribute the full state weight
    assert scored.score >= config.scoring.weights.state


def test_score_process_state_multiplier():
    """State multiplier should reduce sleeping process scores vs running."""
    config = Config()
    collector = TopCollector(config)

    # Same activity, different states
    base_proc = {
        "pid": 1,
        "command": "test",
        "cpu": 50.0,
        "mem": 1000000000,
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "_categories": {"cpu"},
    }

    running_proc = {**base_proc, "state": "running"}
    sleeping_proc = {**base_proc, "state": "sleeping"}

    running_score = collector._score_process(running_proc).score
    sleeping_score = collector._score_process(sleeping_proc).score

    # Running gets 1.0x, sleeping gets its configured multiplier
    sleeping_mult = config.scoring.state_multipliers.sleeping
    assert running_score > sleeping_score
    assert sleeping_score == int(running_score * sleeping_mult)


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


# TopCollector.collect() tests


@pytest.mark.asyncio
async def test_collector_collect(monkeypatch):
    """Collect should run top and return ProcessSamples."""
    config = Config()
    collector = TopCollector(config)

    # Mock _run_top to return sample output
    async def mock_run_top():
        return SAMPLE_TOP_OUTPUT

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    assert isinstance(samples, ProcessSamples)
    assert samples.process_count == 4
    assert samples.max_score > 0
    assert len(samples.rogues) > 0
    assert samples.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_collector_collect_empty_output(monkeypatch):
    """Collect should handle empty top output gracefully."""
    config = Config()
    collector = TopCollector(config)

    # Mock _run_top to return empty output
    async def mock_run_top():
        return ""

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    assert isinstance(samples, ProcessSamples)
    assert samples.process_count == 0
    assert samples.max_score == 0
    assert len(samples.rogues) == 0


@pytest.mark.asyncio
async def test_run_top_command():
    """_run_top should execute top command and return output."""
    config = Config()
    collector = TopCollector(config)

    # Run the actual command (integration-ish test)
    # Skip if not on macOS or if top behaves differently
    import platform

    if platform.system() != "Darwin":
        pytest.skip("top command test only runs on macOS")

    output = await collector._run_top()

    # Should have some output with PID header
    assert "PID" in output
    assert len(output) > 0


def test_score_process_uses_normalization_config():
    """Scoring should use normalization values from config."""
    config = Config()
    # Set a lower memory normalization max - 4GB instead of 8GB
    config.scoring.normalization.mem_gb = 4.0
    collector = TopCollector(config)

    proc = {
        "pid": 1,
        "command": "mem_hog",
        "cpu": 0.0,
        "state": "running",
        "mem": 4 * 1024**3,  # 4GB - should be 1.0 normalized with 4GB max
        "cmprs": 0,
        "pageins": 0,
        "csw": 0,
        "sysbsd": 0,
        "threads": 1,
        "_categories": {"mem"},
    }

    scored = collector._score_process(proc)
    # With 4GB max and 4GB usage, mem contribution should be full (weight=15)
    # Score should be around 15 (mem weight) * 1.0 (state mult for running)
    assert scored.score >= 14  # Allow some rounding

    # Now with default 8GB max, same process should score lower
    config2 = Config()  # Uses default 8GB
    collector2 = TopCollector(config2)
    scored2 = collector2._score_process(proc)
    # With 8GB max and 4GB usage, mem contribution is 0.5 * 15 = 7.5
    assert scored2.score < scored.score


def test_process_score_has_captured_at():
    """ProcessScore includes captured_at timestamp."""
    score = ProcessScore(
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
        score=45,
        categories=frozenset(["cpu"]),
        captured_at=1706000000.0,
    )
    assert score.captured_at == 1706000000.0


def test_process_score_to_dict_includes_captured_at():
    """to_dict() includes captured_at field."""
    score = ProcessScore(
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
        score=45,
        categories=frozenset(["cpu"]),
        captured_at=1706000000.0,
    )
    d = score.to_dict()
    assert d["captured_at"] == 1706000000.0


def test_process_score_from_dict_restores_captured_at():
    """from_dict() restores captured_at field."""
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
        "score": 45,
        "categories": ["cpu"],
        "captured_at": 1706000000.0,
    }
    score = ProcessScore.from_dict(d)
    assert score.captured_at == 1706000000.0
