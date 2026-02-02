"""Tests for metrics collector."""

from datetime import datetime

import pytest

from rogue_hunter.collector import (
    LibprocCollector,
    ProcessSamples,
    ProcessScore,
    count_active_processes,
    get_core_count,
)
from rogue_hunter.config import Config, ScoringConfig

# =============================================================================
# Task 3: Resource-based scoring tests
# =============================================================================


def test_process_score_has_resource_shares():
    """ProcessScore has resource share fields instead of category scores."""
    # Create a ProcessScore with the new fields
    score = ProcessScore(
        pid=123,
        command="test",
        captured_at=datetime.now().timestamp(),
        # Resource shares (new)
        cpu_share=2.5,
        gpu_share=0.0,
        mem_share=1.2,
        disk_share=0.5,
        wakeups_share=0.1,
        disproportionality=2.5,  # Highest share
        dominant_resource="cpu",
        # Raw metrics (unchanged)
        cpu=25.0,
        mem=1024000,
        mem_peak=2048000,
        pageins=0,
        pageins_rate=0.0,
        faults=100,
        faults_rate=10.0,
        disk_io=50000,
        disk_io_rate=5000.0,
        csw=1000,
        csw_rate=100.0,
        syscalls=5000,
        syscalls_rate=500.0,
        threads=4,
        mach_msgs=100,
        mach_msgs_rate=10.0,
        instructions=1000000,
        cycles=2000000,
        ipc=0.5,
        energy=1000,
        energy_rate=100.0,
        wakeups=10,
        wakeups_rate=1.0,
        runnable_time=5000,
        runnable_time_rate=0.5,
        qos_interactive=0,
        qos_interactive_rate=0.0,
        gpu_time=0,
        gpu_time_rate=0.0,
        zombie_children=0,
        state="running",
        priority=31,
        score=45,
        band="elevated",
    )

    assert score.cpu_share == 2.5
    assert score.dominant_resource == "cpu"
    assert score.disproportionality == 2.5

    # Verify round-trip serialization works for new fields
    d = score.to_dict()
    assert d["cpu_share"] == 2.5
    assert d["dominant_resource"] == "cpu"
    assert d["disproportionality"] == 2.5

    restored = ProcessScore.from_dict(d)
    assert restored.cpu_share == 2.5
    assert restored.gpu_share == 0.0
    assert restored.mem_share == 1.2
    assert restored.disk_share == 0.5
    assert restored.wakeups_share == 0.1
    assert restored.dominant_resource == "cpu"
    assert restored.disproportionality == 2.5


def test_process_score_no_category_scores():
    """ProcessScore no longer has category score fields."""
    # These fields should not exist
    fields = ProcessScore.__dataclass_fields__
    assert "blocking_score" not in fields
    assert "contention_score" not in fields
    assert "pressure_score" not in fields
    assert "efficiency_score" not in fields
    assert "dominant_category" not in fields
    assert "dominant_metrics" not in fields


# =============================================================================
# Original tests
# =============================================================================


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
        captured_at=1706000000.0,
        cpu=50.0,
        mem=1000000,
        mem_peak=1000000,
        pageins=10,
        pageins_rate=5.0,
        faults=0,
        faults_rate=0.0,
        disk_io=0,
        disk_io_rate=0.0,
        csw=100,
        csw_rate=50.0,
        syscalls=50,
        syscalls_rate=25.0,
        threads=4,
        mach_msgs=0,
        mach_msgs_rate=0.0,
        instructions=0,
        cycles=0,
        ipc=0.0,
        energy=0,
        energy_rate=0.0,
        wakeups=0,
        wakeups_rate=0.0,
        runnable_time=0,
        runnable_time_rate=0.0,
        qos_interactive=0,
        qos_interactive_rate=0.0,
        gpu_time=0,
        gpu_time_rate=0.0,
        zombie_children=0,
        state="running",
        priority=31,
        score=42,
        band="elevated",
        cpu_share=5.0,
        gpu_share=0.0,
        mem_share=1.0,
        disk_share=0.0,
        wakeups_share=0.0,
        disproportionality=5.0,
        dominant_resource="cpu",
    )
    d = ps.to_dict()
    assert d["pid"] == 123
    assert d["score"] == 42
    assert d["dominant_resource"] == "cpu"
    assert d["disproportionality"] == 5.0


def test_process_score_from_dict():
    """ProcessScore should deserialize from dict."""
    d = {
        "pid": 123,
        "command": "test",
        "captured_at": 1706000000.0,
        "cpu": 50.0,
        "mem": 1000000,
        "mem_peak": 1000000,
        "pageins": 10,
        "pageins_rate": 5.0,
        "faults": 0,
        "faults_rate": 0.0,
        "disk_io": 0,
        "disk_io_rate": 0.0,
        "csw": 100,
        "csw_rate": 50.0,
        "syscalls": 50,
        "syscalls_rate": 25.0,
        "threads": 4,
        "mach_msgs": 0,
        "mach_msgs_rate": 0.0,
        "instructions": 0,
        "cycles": 0,
        "ipc": 0.0,
        "energy": 0,
        "energy_rate": 0.0,
        "wakeups": 0,
        "wakeups_rate": 0.0,
        "runnable_time": 0,
        "runnable_time_rate": 0.0,
        "qos_interactive": 0,
        "qos_interactive_rate": 0.0,
        "gpu_time": 0,
        "gpu_time_rate": 0.0,
        "zombie_children": 0,
        "state": "running",
        "priority": 31,
        "score": 42,
        "band": "elevated",
        "cpu_share": 5.0,
        "gpu_share": 0.0,
        "mem_share": 1.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
        "disproportionality": 5.0,
        "dominant_resource": "cpu",
    }
    ps = ProcessScore.from_dict(d)
    assert ps.pid == 123
    assert ps.dominant_resource == "cpu"
    assert ps.disproportionality == 5.0


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
                captured_at=1706000000.0,
                cpu=80.0,
                mem=1000,
                mem_peak=1000,
                pageins=0,
                pageins_rate=0.0,
                faults=0,
                faults_rate=0.0,
                disk_io=0,
                disk_io_rate=0.0,
                csw=10,
                csw_rate=0.0,
                syscalls=5,
                syscalls_rate=0.0,
                threads=2,
                mach_msgs=0,
                mach_msgs_rate=0.0,
                instructions=0,
                cycles=0,
                ipc=0.0,
                energy=0,
                energy_rate=0.0,
                wakeups=0,
                wakeups_rate=0.0,
                runnable_time=0,
                runnable_time_rate=0.0,
                qos_interactive=0,
                qos_interactive_rate=0.0,
                gpu_time=0,
                gpu_time_rate=0.0,
                zombie_children=0,
                state="running",
                priority=31,
                score=75,
                band="high",
                cpu_share=8.0,
                gpu_share=0.0,
                mem_share=0.1,
                disk_share=0.0,
                wakeups_share=0.0,
                disproportionality=8.0,
                dominant_resource="cpu",
            ),
        ],
    )
    json_str = samples.to_json()
    restored = ProcessSamples.from_json(json_str)
    assert restored.max_score == 75
    assert len(restored.rogues) == 1
    assert restored.rogues[0].command == "test"


# =============================================================================
# LibprocCollector tests
# =============================================================================


class TestLibprocCollectorInit:
    """Test LibprocCollector initialization."""

    def test_init_creates_empty_prev_samples(self):
        """Collector starts with no previous samples."""
        config = Config()
        collector = LibprocCollector(config)
        assert collector._prev_samples == {}
        assert collector._last_collect_time == 0.0

    def test_init_loads_timebase(self):
        """Collector loads mach timebase info."""
        config = Config()
        collector = LibprocCollector(config)
        assert collector._timebase.numer > 0
        assert collector._timebase.denom > 0


class TestLibprocCollectorSync:
    """Test LibprocCollector synchronous collection."""

    def test_collect_sync_returns_process_samples(self):
        """_collect_sync returns ProcessSamples."""
        config = Config()
        collector = LibprocCollector(config)

        samples = collector._collect_sync()

        assert isinstance(samples, ProcessSamples)
        assert samples.process_count > 0  # Should see some processes
        assert samples.elapsed_ms >= 0

    def test_collect_sync_populates_prev_samples(self):
        """First collection populates _prev_samples dict."""
        config = Config()
        collector = LibprocCollector(config)

        assert len(collector._prev_samples) == 0
        collector._collect_sync()
        assert len(collector._prev_samples) > 0

    def test_collect_sync_first_sample_has_zero_cpu(self):
        """First sample has 0% CPU (no baseline for delta)."""
        config = Config()
        collector = LibprocCollector(config)

        samples = collector._collect_sync()

        # All processes should have 0% CPU on first sample
        for rogue in samples.rogues:
            assert rogue.cpu == 0.0

    def test_collect_sync_second_sample_has_nonzero_cpu(self):
        """Second sample can have non-zero CPU% (delta from first)."""
        import time

        config = Config()
        collector = LibprocCollector(config)

        # First collection establishes baseline
        collector._collect_sync()

        # Wait a tiny bit to allow some CPU to be used
        time.sleep(0.05)

        # Second collection has delta
        samples = collector._collect_sync()

        # At least one process should have non-zero CPU (our own Python process)
        # But we can't guarantee this, so just check it doesn't crash
        assert isinstance(samples, ProcessSamples)

    def test_collect_sync_prunes_stale_pids(self):
        """Processes that disappear are pruned from _prev_samples."""
        config = Config()
        collector = LibprocCollector(config)

        # First collection
        collector._collect_sync()

        # Add a fake PID that doesn't exist
        fake_pid = 999999999
        from rogue_hunter.collector import _PrevSample

        collector._prev_samples[fake_pid] = _PrevSample(
            cpu_time_ns=0,
            timestamp=0.0,
            disk_io=0,
            energy=0,
            pageins=0,
            csw=0,
            syscalls=0,
            mach_msgs=0,
            wakeups=0,
            faults=0,
            runnable_time=0,
            qos_interactive=0,
            gpu_time=0,
        )

        # Second collection should prune the fake PID
        collector._collect_sync()

        assert fake_pid not in collector._prev_samples
        # Real PIDs should still be tracked (mostly)
        assert len(collector._prev_samples) > 0


class TestLibprocCollectorAsync:
    """Test LibprocCollector async collect method."""

    @pytest.mark.asyncio
    async def test_collect_runs_in_executor(self):
        """collect() runs _collect_sync in executor."""
        config = Config()
        collector = LibprocCollector(config)

        samples = await collector.collect()

        assert isinstance(samples, ProcessSamples)
        assert samples.process_count > 0

    @pytest.mark.asyncio
    async def test_collect_returns_valid_rogues(self):
        """collect() returns rogues with valid ProcessScore objects."""
        import platform

        if platform.system() != "Darwin":
            pytest.skip("LibprocCollector only works on macOS")

        config = Config()
        collector = LibprocCollector(config)

        samples = await collector.collect()

        for rogue in samples.rogues:
            assert isinstance(rogue, ProcessScore)
            assert rogue.pid > 0
            assert isinstance(rogue.command, str)
            assert rogue.cpu >= 0.0
            assert rogue.mem >= 0
            assert rogue.score >= 0


class TestLibprocCollectorRogueSelection:
    """Test LibprocCollector rogue selection logic."""

    def test_select_rogues_stuck_always_included(self):
        """Stuck processes are always selected regardless of score."""
        from tests.conftest import make_process_score

        config = Config()
        collector = LibprocCollector(config)

        # Create a stuck process with low score
        stuck = make_process_score(
            pid=2,
            command="stuck_process",
            score=5,  # Below threshold
            state="stuck",
        )
        # Create a normal sleeping process with high score
        normal = make_process_score(
            pid=1,
            command="normal",
            score=50,  # Above threshold
            state="sleeping",
        )

        # _select_rogues now takes ProcessScore objects
        selected = collector._select_rogues([stuck, normal])

        # Stuck process should be included despite low score
        stuck_found = [r for r in selected if r.command == "stuck_process"]
        assert len(stuck_found) == 1


class TestLibprocCollectorIntegration:
    """Integration tests for complete collection cycle."""

    @pytest.mark.asyncio
    async def test_full_collection_cycle(self):
        """Full collection cycle produces valid output."""
        import platform

        if platform.system() != "Darwin":
            pytest.skip("LibprocCollector only works on macOS")

        config = Config()
        collector = LibprocCollector(config)

        # First collection
        samples1 = await collector.collect()
        assert isinstance(samples1, ProcessSamples)

        # Second collection (should have rate data)
        import asyncio

        await asyncio.sleep(0.1)
        samples2 = await collector.collect()
        assert isinstance(samples2, ProcessSamples)

        # Check rogues have all required resource share fields
        for rogue in samples2.rogues:
            assert hasattr(rogue, "cpu_share")
            assert hasattr(rogue, "gpu_share")
            assert hasattr(rogue, "mem_share")
            assert hasattr(rogue, "disk_share")
            assert hasattr(rogue, "wakeups_share")
            assert hasattr(rogue, "disproportionality")
            assert hasattr(rogue, "dominant_resource")


# =============================================================================
# Task 5: Active process counting tests
# =============================================================================


def test_count_active_processes_excludes_idle():
    """Idle processes are not counted as active."""
    processes = [
        {"state": "running", "cpu": 5.0, "mem": 100_000_000, "disk_io_rate": 0},
        {"state": "idle", "cpu": 1.0, "mem": 50_000_000, "disk_io_rate": 0},  # idle = excluded
        {"state": "sleeping", "cpu": 0.5, "mem": 200_000_000, "disk_io_rate": 100},
    ]
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 2  # idle process excluded


def test_count_active_processes_excludes_no_resources():
    """Processes using no resources are not counted as active."""
    processes = [
        {"state": "running", "cpu": 5.0, "mem": 100_000_000, "disk_io_rate": 0},
        {"state": "sleeping", "cpu": 0.0, "mem": 0, "disk_io_rate": 0},  # no resources = excluded
        {"state": "running", "cpu": 0.0, "mem": 0, "disk_io_rate": 1000},  # has disk I/O = included
    ]
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 2  # zero-resource process excluded


def test_count_active_processes_respects_thresholds():
    """Active thresholds from config are respected."""
    processes = [
        # Below all thresholds - not counted
        {"state": "running", "cpu": 0.05, "mem": 5_000_000, "disk_io_rate": 0},
        # CPU above threshold - counted
        {"state": "running", "cpu": 0.2, "mem": 5_000_000, "disk_io_rate": 0},
    ]
    config = ScoringConfig(active_min_cpu=0.1, active_min_memory_mb=10.0, active_min_disk_io=0)

    count = count_active_processes(processes, config)

    assert count == 1  # only process with cpu > 0.1 counts


def test_count_active_processes_minimum_one():
    """Active process count is at least 1 to avoid division by zero."""
    processes = []  # No processes
    config = ScoringConfig()

    count = count_active_processes(processes, config)

    assert count == 1  # Minimum of 1


# =============================================================================
# Task 6: Fair share calculation tests
# =============================================================================


def test_calculate_resource_shares_basic():
    """Resource shares are calculated as multiples of fair share."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {
            "pid": 1,
            "cpu": 50.0,
            "gpu_time_rate": 0,
            "mem": 1_000_000_000,
            "disk_io_rate": 1000,
            "wakeups_rate": 10,
        },
        {
            "pid": 2,
            "cpu": 50.0,
            "gpu_time_rate": 0,
            "mem": 1_000_000_000,
            "disk_io_rate": 1000,
            "wakeups_rate": 10,
        },
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # Each process uses 50% of total CPU (50 / 100 total)
    # Fair share = 1/2 = 50%
    # Share ratio = 50% / 50% = 1.0 (exactly fair)
    assert shares[1]["cpu_share"] == 1.0
    assert shares[2]["cpu_share"] == 1.0


def test_calculate_resource_shares_disproportionate():
    """Process using more than fair share has share > 1."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {
            "pid": 1,
            "cpu": 90.0,
            "gpu_time_rate": 0,
            "mem": 500_000_000,
            "disk_io_rate": 0,
            "wakeups_rate": 0,
        },
        {
            "pid": 2,
            "cpu": 10.0,
            "gpu_time_rate": 0,
            "mem": 500_000_000,
            "disk_io_rate": 0,
            "wakeups_rate": 0,
        },
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # Process 1: 90% of 100% total = 90% usage
    # Fair share = 50%
    # Share ratio = 90% / 50% = 1.8
    assert shares[1]["cpu_share"] == 1.8
    # Process 2: 10% / 50% = 0.2
    assert shares[2]["cpu_share"] == 0.2


def test_calculate_resource_shares_zero_total():
    """When total resource is zero, all shares are zero."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {
            "pid": 1,
            "cpu": 0,
            "gpu_time_rate": 0,
            "mem": 1_000_000,
            "disk_io_rate": 0,
            "wakeups_rate": 0,
        },
        {
            "pid": 2,
            "cpu": 0,
            "gpu_time_rate": 0,
            "mem": 1_000_000,
            "disk_io_rate": 0,
            "wakeups_rate": 0,
        },
    ]
    active_count = 2

    shares = calculate_resource_shares(processes, active_count)

    # No CPU usage, so CPU share is 0
    assert shares[1]["cpu_share"] == 0.0
    assert shares[2]["cpu_share"] == 0.0


def test_calculate_resource_shares_all_resources():
    """Shares calculated for all resource types."""
    from rogue_hunter.collector import calculate_resource_shares

    processes = [
        {
            "pid": 1,
            "cpu": 100,
            "gpu_time_rate": 50,
            "mem": 2_000_000_000,
            "disk_io_rate": 5000,
            "wakeups_rate": 100,
        },
    ]
    active_count = 1

    shares = calculate_resource_shares(processes, active_count)

    # Single process = uses 100% of all resources = 1.0 share (exactly fair when alone)
    assert shares[1]["cpu_share"] == 1.0
    assert shares[1]["gpu_share"] == 1.0
    assert shares[1]["mem_share"] == 1.0
    assert shares[1]["disk_share"] == 1.0
    assert shares[1]["wakeups_share"] == 1.0


# =============================================================================
# Task 7: Disproportionate-share scoring tests
# =============================================================================


def test_score_from_shares_applies_weights():
    """Score calculation applies resource weights from config."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    shares = {
        "cpu_share": 10.0,  # 10x fair share
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }
    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)

    score, dominant, disproportionality = score_from_shares(shares, weights)

    assert dominant == "cpu"
    assert disproportionality == 10.0
    assert score > 0


def test_score_from_shares_gpu_weighted_higher():
    """GPU share contributes more to score than equal CPU share."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)

    cpu_shares = {
        "cpu_share": 10.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }
    gpu_shares = {
        "cpu_share": 0.0,
        "gpu_share": 10.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }

    cpu_score, _, _ = score_from_shares(cpu_shares, weights)
    gpu_score, _, _ = score_from_shares(gpu_shares, weights)

    assert gpu_score > cpu_score  # GPU weighted 3x, so higher score


def test_score_from_shares_logarithmic_curve():
    """Score uses logarithmic curve - diminishing returns at extremes."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()

    shares_10x = {
        "cpu_share": 10.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }
    shares_100x = {
        "cpu_share": 100.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }
    shares_1000x = {
        "cpu_share": 1000.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }

    score_10x, _, _ = score_from_shares(shares_10x, weights)
    score_100x, _, _ = score_from_shares(shares_100x, weights)
    score_1000x, _, _ = score_from_shares(shares_1000x, weights)

    increase_10_to_100 = score_100x - score_10x
    increase_100_to_1000 = score_1000x - score_100x

    assert increase_100_to_1000 < increase_10_to_100 * 2  # Diminishing returns


def test_score_from_shares_critical_reachable():
    """Critical band (70+) is reachable with extreme disproportionality."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()
    shares = {
        "cpu_share": 200.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }

    score, _, _ = score_from_shares(shares, weights)

    assert score >= 70  # Critical band


def test_score_from_shares_high_reachable_under_load():
    """High band (50-69) is reachable with moderate disproportionality."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()
    shares = {
        "cpu_share": 75.0,
        "gpu_share": 0.0,
        "mem_share": 0.0,
        "disk_share": 0.0,
        "wakeups_share": 0.0,
    }

    score, _, _ = score_from_shares(shares, weights)

    assert 50 <= score < 70  # High band


def test_score_from_shares_dominant_resource():
    """Dominant resource is the one with highest weighted share."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights(cpu=1.0, gpu=3.0, memory=1.0, disk_io=1.0, wakeups=2.0)
    shares = {
        "cpu_share": 10.0,
        "gpu_share": 5.0,
        "mem_share": 2.0,
        "disk_share": 1.0,
        "wakeups_share": 1.0,
    }

    _, dominant, disproportionality = score_from_shares(shares, weights)

    # GPU: 5.0 * 3.0 = 15.0 weighted
    # CPU: 10.0 * 1.0 = 10.0 weighted
    assert dominant == "gpu"
    assert disproportionality == 5.0  # Raw share of dominant resource


def test_score_from_shares_clamped_to_100():
    """Score is clamped to maximum of 100."""
    from rogue_hunter.collector import score_from_shares
    from rogue_hunter.config import ResourceWeights

    weights = ResourceWeights()
    shares = {
        "cpu_share": 10000.0,
        "gpu_share": 10000.0,
        "mem_share": 10000.0,
        "disk_share": 10000.0,
        "wakeups_share": 10000.0,
    }

    score, _, _ = score_from_shares(shares, weights)

    assert score == 100


# =============================================================================
# Task 8: Integration tests - new scoring in collector
# =============================================================================


class TestCollectorNewScoringIntegration:
    """Tests that collector properly integrates the new resource-based scoring."""

    def test_collector_uses_new_scoring(self):
        """Verifies ProcessScore has new fields and not old category fields."""
        import platform

        if platform.system() != "Darwin":
            pytest.skip("LibprocCollector only works on macOS")

        config = Config()
        collector = LibprocCollector(config)

        # Run two collections to get rate data
        collector._collect_sync()
        import time

        time.sleep(0.05)
        samples = collector._collect_sync()

        # Verify ProcessScore has new resource share fields
        for rogue in samples.rogues:
            # New fields should exist and be populated
            assert hasattr(rogue, "cpu_share")
            assert hasattr(rogue, "gpu_share")
            assert hasattr(rogue, "mem_share")
            assert hasattr(rogue, "disk_share")
            assert hasattr(rogue, "wakeups_share")
            assert hasattr(rogue, "disproportionality")
            assert hasattr(rogue, "dominant_resource")

            # Dominant resource should be a valid resource type
            assert rogue.dominant_resource in ("cpu", "gpu", "memory", "disk", "wakeups")

        # Old category fields should NOT exist on ProcessScore
        fields = ProcessScore.__dataclass_fields__
        assert "blocking_score" not in fields
        assert "contention_score" not in fields
        assert "pressure_score" not in fields
        assert "efficiency_score" not in fields
        assert "dominant_category" not in fields

    def test_collector_calculates_active_count(self):
        """Verifies count_active_processes is called with correct args."""
        import platform
        from unittest.mock import patch

        if platform.system() != "Darwin":
            pytest.skip("LibprocCollector only works on macOS")

        config = Config()
        collector = LibprocCollector(config)

        # Mock count_active_processes to verify it's called
        with patch(
            "rogue_hunter.collector.count_active_processes", wraps=count_active_processes
        ) as mock_count:
            # First collection
            collector._collect_sync()
            import time

            time.sleep(0.05)
            # Second collection should use the function
            collector._collect_sync()

            # Verify count_active_processes was called
            assert mock_count.call_count >= 1

            # Verify it was called with a list of process dicts and the scoring config
            call_args = mock_count.call_args
            processes_arg = call_args[0][0]
            config_arg = call_args[0][1]

            assert isinstance(processes_arg, list)
            assert len(processes_arg) > 0
            # Each process should be a dict with expected keys
            assert "pid" in processes_arg[0]
            assert "cpu" in processes_arg[0]
            assert "mem" in processes_arg[0]
            assert "state" in processes_arg[0]
            # Config should be the scoring config
            assert config_arg == config.scoring
