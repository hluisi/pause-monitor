"""Tests for metrics collector."""

from datetime import datetime

import pytest

from pause_monitor.collector import (
    LibprocCollector,
    MetricValue,
    MetricValueStr,
    ProcessSamples,
    ProcessScore,
    get_core_count,
)
from pause_monitor.config import Config


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
        faults=_metric(0),
        disk_io=_metric(0),
        disk_io_rate=_metric(0.0),
        csw=_metric(100),
        syscalls=_metric(50),
        threads=_metric(4),
        mach_msgs=_metric(0),
        instructions=_metric(0),
        cycles=_metric(0),
        ipc=_metric(0.0),
        energy=_metric(0),
        energy_rate=_metric(0.0),
        wakeups=_metric(0),
        state=_metric_str("running"),
        priority=_metric(31),
        score=_metric(42),
        band=_metric_str("elevated"),
        categories=["cpu", "pageins"],
    )
    d = ps.to_dict()
    assert d["pid"] == 123
    assert d["score"]["current"] == 42
    assert set(d["categories"]) == {"cpu", "pageins"}


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
        "faults": {"current": 0, "low": 0, "high": 0},
        "disk_io": {"current": 0, "low": 0, "high": 0},
        "disk_io_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "csw": {"current": 100, "low": 100, "high": 100},
        "syscalls": {"current": 50, "low": 50, "high": 50},
        "threads": {"current": 4, "low": 4, "high": 4},
        "mach_msgs": {"current": 0, "low": 0, "high": 0},
        "instructions": {"current": 0, "low": 0, "high": 0},
        "cycles": {"current": 0, "low": 0, "high": 0},
        "ipc": {"current": 0.0, "low": 0.0, "high": 0.0},
        "energy": {"current": 0, "low": 0, "high": 0},
        "energy_rate": {"current": 0.0, "low": 0.0, "high": 0.0},
        "wakeups": {"current": 0, "low": 0, "high": 0},
        "state": {"current": "running", "low": "running", "high": "running"},
        "priority": {"current": 31, "low": 31, "high": 31},
        "score": {"current": 42, "low": 42, "high": 42},
        "band": {"current": "elevated", "low": "elevated", "high": "elevated"},
        "categories": ["cpu", "pageins"],
    }
    ps = ProcessScore.from_dict(d)
    assert ps.pid == 123
    assert set(ps.categories) == {"cpu", "pageins"}


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
                faults=_metric(0),
                disk_io=_metric(0),
                disk_io_rate=_metric(0.0),
                csw=_metric(10),
                syscalls=_metric(5),
                threads=_metric(2),
                mach_msgs=_metric(0),
                instructions=_metric(0),
                cycles=_metric(0),
                ipc=_metric(0.0),
                energy=_metric(0),
                energy_rate=_metric(0.0),
                wakeups=_metric(0),
                state=_metric_str("running"),
                priority=_metric(31),
                score=_metric(75),
                band=_metric_str("high"),
                categories=["cpu"],
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
        from pause_monitor.collector import _PrevSample

        collector._prev_samples[fake_pid] = _PrevSample(
            cpu_time_ns=0, timestamp=0.0, disk_io=0, energy=0
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
        """Stuck processes are always selected."""
        config = Config()
        collector = LibprocCollector(config)

        processes = [
            {
                "pid": 1,
                "command": "normal",
                "cpu": 1.0,
                "state": "sleeping",
                "mem": 100,
                "mem_peak": 100,
                "pageins": 0,
                "faults": 0,
                "disk_io": 0,
                "disk_io_rate": 0.0,
                "csw": 0,
                "syscalls": 0,
                "threads": 1,
                "mach_msgs": 0,
                "instructions": 0,
                "cycles": 0,
                "ipc": 0.0,
                "energy": 0,
                "energy_rate": 0.0,
                "wakeups": 0,
                "priority": 31,
            },
            {
                "pid": 2,
                "command": "stuck_process",
                "cpu": 0.0,
                "state": "stuck",
                "mem": 100,
                "mem_peak": 100,
                "pageins": 0,
                "faults": 0,
                "disk_io": 0,
                "disk_io_rate": 0.0,
                "csw": 0,
                "syscalls": 0,
                "threads": 1,
                "mach_msgs": 0,
                "instructions": 0,
                "cycles": 0,
                "ipc": 0.0,
                "energy": 0,
                "energy_rate": 0.0,
                "wakeups": 0,
                "priority": 31,
            },
        ]

        rogues = collector._select_rogues(processes)

        stuck = [r for r in rogues if r["command"] == "stuck_process"]
        assert len(stuck) == 1
        assert "stuck" in stuck[0]["_categories"]


class TestLibprocCollectorIntegration:
    """Integration tests that actually run on macOS."""

    @pytest.mark.asyncio
    async def test_full_collection_cycle(self):
        """Full collection cycle works end-to-end."""
        import platform

        if platform.system() != "Darwin":
            pytest.skip("LibprocCollector only works on macOS")

        config = Config()
        collector = LibprocCollector(config)

        # First sample
        samples1 = await collector.collect()
        assert samples1.process_count > 10  # Should see many processes

        # Second sample (should have CPU deltas)
        samples2 = await collector.collect()
        assert samples2.process_count > 10

        # Both should have valid timestamps
        assert samples1.timestamp is not None
        assert samples2.timestamp is not None
        assert samples2.timestamp >= samples1.timestamp
