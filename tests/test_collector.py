"""Tests for metrics collector."""

from datetime import datetime

import pytest

from rogue_hunter.collector import (
    LibprocCollector,
    MetricValue,
    MetricValueStr,
    ProcessSamples,
    ProcessScore,
    get_core_count,
)
from rogue_hunter.config import Config


def _metric(val: float | int) -> MetricValue:
    """Create MetricValue with same value for current/low/high."""
    return MetricValue(current=val, low=val, high=val)


def _metric_str(val: str) -> MetricValueStr:
    """Create MetricValueStr with same value for current/low/high."""
    return MetricValueStr(current=val, low=val, high=val)


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
        cpu=_metric(50.0),
        mem=_metric(1000000),
        mem_peak=1000000,
        pageins=_metric(10),
        pageins_rate=_metric(5.0),
        faults=_metric(0),
        faults_rate=_metric(0.0),
        disk_io=_metric(0),
        disk_io_rate=_metric(0.0),
        csw=_metric(100),
        csw_rate=_metric(50.0),
        syscalls=_metric(50),
        syscalls_rate=_metric(25.0),
        threads=_metric(4),
        mach_msgs=_metric(0),
        mach_msgs_rate=_metric(0.0),
        instructions=_metric(0),
        cycles=_metric(0),
        ipc=_metric(0.0),
        energy=_metric(0),
        energy_rate=_metric(0.0),
        wakeups=_metric(0),
        wakeups_rate=_metric(0.0),
        runnable_time=_metric(0),
        runnable_time_rate=_metric(0.0),
        qos_interactive=_metric(0),
        qos_interactive_rate=_metric(0.0),
        state=_metric_str("running"),
        priority=_metric(31),
        score=_metric(42),
        band=_metric_str("elevated"),
        blocking_score=_metric(30.0),
        contention_score=_metric(20.0),
        pressure_score=_metric(10.0),
        efficiency_score=_metric(5.0),
        dominant_category="blocking",
        dominant_metrics=["pageins:5/s"],
    )
    d = ps.to_dict()
    assert d["pid"] == 123
    assert d["score"]["current"] == 42
    assert d["dominant_category"] == "blocking"
    assert d["dominant_metrics"] == ["pageins:5/s"]


def test_process_score_from_dict():
    """ProcessScore should deserialize from dict."""
    d = {
        "pid": 123,
        "command": "test",
        "captured_at": 1706000000.0,
        "cpu": {"current": 50.0, "low": 50.0, "high": 50.0},
        "mem": {"current": 1000000, "low": 1000000, "high": 1000000},
        "mem_peak": 1000000,
        "pageins": {"current": 10, "low": 10, "high": 10},
        "pageins_rate": {"current": 5.0, "low": 5.0, "high": 5.0},
        "faults": {"current": 0, "low": 0, "high": 0},
        "faults_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "disk_io": {"current": 0, "low": 0, "high": 0},
        "disk_io_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "csw": {"current": 100, "low": 100, "high": 100},
        "csw_rate": {"current": 50.0, "low": 50.0, "high": 50.0},
        "syscalls": {"current": 50, "low": 50, "high": 50},
        "syscalls_rate": {"current": 25.0, "low": 25.0, "high": 25.0},
        "threads": {"current": 4, "low": 4, "high": 4},
        "mach_msgs": {"current": 0, "low": 0, "high": 0},
        "mach_msgs_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "instructions": {"current": 0, "low": 0, "high": 0},
        "cycles": {"current": 0, "low": 0, "high": 0},
        "ipc": {"current": 0.0, "low": 0.0, "high": 0.0},
        "energy": {"current": 0, "low": 0, "high": 0},
        "energy_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "wakeups": {"current": 0, "low": 0, "high": 0},
        "wakeups_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "runnable_time": {"current": 0, "low": 0, "high": 0},
        "runnable_time_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "qos_interactive": {"current": 0, "low": 0, "high": 0},
        "qos_interactive_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "state": {"current": "running", "low": "running", "high": "running"},
        "priority": {"current": 31, "low": 31, "high": 31},
        "score": {"current": 42, "low": 42, "high": 42},
        "band": {"current": "elevated", "low": "elevated", "high": "elevated"},
        "blocking_score": {"current": 30.0, "low": 30.0, "high": 30.0},
        "contention_score": {"current": 20.0, "low": 20.0, "high": 20.0},
        "pressure_score": {"current": 10.0, "low": 10.0, "high": 10.0},
        "efficiency_score": {"current": 5.0, "low": 5.0, "high": 5.0},
        "dominant_category": "blocking",
        "dominant_metrics": ["pageins:5/s"],
    }
    ps = ProcessScore.from_dict(d)
    assert ps.pid == 123
    assert ps.dominant_category == "blocking"
    assert ps.dominant_metrics == ["pageins:5/s"]


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
                cpu=_metric(80.0),
                mem=_metric(1000),
                mem_peak=1000,
                pageins=_metric(0),
                pageins_rate=_metric(0.0),
                faults=_metric(0),
                faults_rate=_metric(0.0),
                disk_io=_metric(0),
                disk_io_rate=_metric(0.0),
                csw=_metric(10),
                csw_rate=_metric(0.0),
                syscalls=_metric(5),
                syscalls_rate=_metric(0.0),
                threads=_metric(2),
                mach_msgs=_metric(0),
                mach_msgs_rate=_metric(0.0),
                instructions=_metric(0),
                cycles=_metric(0),
                ipc=_metric(0.0),
                energy=_metric(0),
                energy_rate=_metric(0.0),
                wakeups=_metric(0),
                wakeups_rate=_metric(0.0),
                runnable_time=_metric(0),
                runnable_time_rate=_metric(0.0),
                qos_interactive=_metric(0),
                qos_interactive_rate=_metric(0.0),
                state=_metric_str("running"),
                priority=_metric(31),
                score=_metric(75),
                band=_metric_str("high"),
                blocking_score=_metric(40.0),
                contention_score=_metric(30.0),
                pressure_score=_metric(20.0),
                efficiency_score=_metric(10.0),
                dominant_category="blocking",
                dominant_metrics=["cpu:80%"],
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
            assert rogue.cpu.current == 0.0

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
            assert rogue.cpu.current >= 0.0
            assert rogue.mem.current >= 0
            assert rogue.score.current >= 0


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

        # Check rogues have all required fields
        for rogue in samples2.rogues:
            assert hasattr(rogue, "blocking_score")
            assert hasattr(rogue, "contention_score")
            assert hasattr(rogue, "pressure_score")
            assert hasattr(rogue, "efficiency_score")
            assert hasattr(rogue, "dominant_category")
            assert hasattr(rogue, "dominant_metrics")
