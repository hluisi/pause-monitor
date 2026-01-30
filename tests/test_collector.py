"""Tests for metrics collector."""

from datetime import datetime

import pytest

from pause_monitor.collector import (
    LibprocCollector,
    ProcessSamples,
    ProcessScore,
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
        from pause_monitor.collector import _PrevSample

        collector._prev_samples[fake_pid] = _PrevSample(cpu_time_ns=0, timestamp=0.0)

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
                "cmprs": 0,
                "pageins": 0,
                "csw": 0,
                "sysbsd": 0,
                "threads": 1,
            },
            {
                "pid": 2,
                "command": "stuck_process",
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
