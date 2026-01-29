"""Integration tests for per-process scoring feature.

Tests the end-to-end data flow:
  TopCollector -> ProcessSamples -> RingBuffer -> Storage -> Socket

These tests use mocks where appropriate to avoid running actual system commands.
"""

import asyncio
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.collector import ProcessSamples, ProcessScore, TopCollector
from pause_monitor.config import Config
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.socket_server import SocketServer

# --- Test Fixtures ---


@pytest.fixture
def sample_top_output() -> str:
    """Sample top command output for mocking."""
    return """
Processes: 500 total, 3 running, 497 sleeping, 4000 threads
2026/01/23 12:00:00
Load Avg: 2.00, 1.50, 1.00

PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
7229   chrome           85.1 running  1024M  50M    38     1134810    3961273    500
409    WindowServer     27.7 running  1473M  0B     26     84562346   103373638  3427
0      kernel_task      18.1 stuck    43M    0B     870    793476910  0          0
620    zombie_proc      0.0  zombie   0B     0B     0      0          0          0
1234   node             45.5 running  512M   20M    16     50000      100000     100
"""


@pytest.fixture
def short_tmp_path():
    """Create a short temporary path for Unix sockets.

    macOS has a 104-character limit for Unix socket paths.
    pytest's tmp_path is too long, so we use /tmp directly.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="pm_int_") as tmpdir:
        yield Path(tmpdir)


def make_test_process_score(**kwargs) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    defaults = {
        "pid": 1,
        "command": "test_proc",
        "cpu": 50.0,
        "state": "running",
        "mem": 1024 * 1024,  # 1MB
        "cmprs": 0,
        "pageins": 10,
        "csw": 100,
        "sysbsd": 50,
        "threads": 4,
        "score": 50,
        "categories": frozenset({"cpu"}),
        "captured_at": time.time(),
    }
    defaults.update(kwargs)
    return ProcessScore(**defaults)


def make_test_samples(**kwargs) -> ProcessSamples:
    """Create ProcessSamples with sensible defaults for testing."""
    defaults = {
        "timestamp": datetime.now(),
        "elapsed_ms": 1000,
        "process_count": 100,
        "max_score": 50,
        "rogues": [],
    }
    defaults.update(kwargs)
    return ProcessSamples(**defaults)


# --- Integration Test: TopCollector -> ProcessSamples ---


@pytest.mark.asyncio
async def test_collector_produces_process_samples(monkeypatch, sample_top_output):
    """TopCollector.collect() should parse top output and produce scored ProcessSamples."""
    config = Config()
    collector = TopCollector(config)

    # Mock _run_top to return sample output
    async def mock_run_top():
        return sample_top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    # Verify ProcessSamples structure
    assert isinstance(samples, ProcessSamples)
    assert samples.process_count == 5
    assert samples.elapsed_ms >= 0
    assert samples.timestamp is not None

    # Verify rogues are selected and scored
    assert len(samples.rogues) > 0
    assert all(isinstance(r, ProcessScore) for r in samples.rogues)

    # Verify max_score is correctly computed
    assert samples.max_score == max(r.score for r in samples.rogues)

    # Verify high-stress processes are included
    rogue_commands = {r.command for r in samples.rogues}
    assert "chrome" in rogue_commands  # High CPU
    assert "kernel_task" in rogue_commands  # Stuck state


@pytest.mark.asyncio
async def test_collector_scoring_produces_differentiated_scores(monkeypatch, sample_top_output):
    """Processes with different stress levels should have different scores."""
    config = Config()
    collector = TopCollector(config)

    async def mock_run_top():
        return sample_top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    scores_by_command = {r.command: r.score for r in samples.rogues}

    # Chrome (85% CPU + pageins) should score higher than WindowServer (27% CPU)
    if "chrome" in scores_by_command and "WindowServer" in scores_by_command:
        assert scores_by_command["chrome"] > scores_by_command["WindowServer"]

    # Stuck kernel_task should have a significant score despite low CPU
    if "kernel_task" in scores_by_command:
        assert scores_by_command["kernel_task"] >= 15  # State weight contributes


# --- Integration Test: ProcessSamples -> Storage -> Retrieval ---


# --- Integration Test: ProcessSamples -> RingBuffer -> Freeze ---


def test_ring_buffer_stores_process_samples():
    """RingBuffer should store ProcessSamples and maintain order."""
    buffer = RingBuffer(max_samples=10)

    # Push samples with increasing scores
    for i in range(5):
        samples = make_test_samples(
            max_score=10 + i * 10,
            rogues=[make_test_process_score(pid=i, score=10 + i * 10)],
        )
        buffer.push(samples)

    assert len(buffer) == 5

    ring_samples = buffer.samples
    scores = [rs.samples.max_score for rs in ring_samples]
    assert scores == [10, 20, 30, 40, 50]


def test_ring_buffer_freeze_captures_state():
    """freeze() should return immutable snapshot of ProcessSamples."""
    buffer = RingBuffer(max_samples=10)

    samples = make_test_samples(
        max_score=75,
        rogues=[
            make_test_process_score(pid=1, command="proc1", score=75),
            make_test_process_score(pid=2, command="proc2", score=60),
        ],
    )
    buffer.push(samples)

    frozen = buffer.freeze()

    # Verify frozen state
    assert len(frozen.samples) == 1
    assert frozen.samples[0].samples.max_score == 75
    assert len(frozen.samples[0].samples.rogues) == 2

    # Verify immutability (tuple)
    assert isinstance(frozen.samples, tuple)


def test_ring_buffer_respects_max_samples():
    """RingBuffer should evict oldest samples when full."""
    buffer = RingBuffer(max_samples=3)

    # Push 5 samples
    for i in range(5):
        samples = make_test_samples(
            max_score=i * 10,
            rogues=[make_test_process_score(pid=i, score=i * 10)],
        )
        buffer.push(samples)

    assert len(buffer) == 3

    # Should have the last 3 samples (indices 2, 3, 4)
    ring_samples = buffer.samples
    scores = [rs.samples.max_score for rs in ring_samples]
    assert scores == [20, 30, 40]


# --- Integration Test: Full Cycle with Socket ---


@pytest.mark.asyncio
async def test_full_collection_to_socket_cycle(monkeypatch, short_tmp_path, sample_top_output):
    """Test complete collection -> buffer -> socket broadcast cycle."""
    socket_path = short_tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=30)

    # Create collector with mocked top command
    config = Config()
    collector = TopCollector(config)

    async def mock_run_top():
        return sample_top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    # Start socket server
    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect a client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read initial state (empty buffer)
        initial_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        initial_msg = json.loads(initial_data.decode())
        assert initial_msg["type"] == "initial_state"
        assert initial_msg["samples"] == []

        # Collect samples
        samples = await collector.collect()
        assert samples.max_score > 0

        # Push to ring buffer
        buffer.push(samples)

        # Broadcast to clients
        await server.broadcast(samples)

        # Verify client receives broadcast
        broadcast_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        broadcast_msg = json.loads(broadcast_data.decode())

        assert broadcast_msg["type"] == "sample"
        assert broadcast_msg["max_score"] == samples.max_score
        assert broadcast_msg["process_count"] == samples.process_count
        assert len(broadcast_msg["rogues"]) == len(samples.rogues)

        # Verify rogue data in broadcast
        chrome_in_broadcast = next(
            (r for r in broadcast_msg["rogues"] if r["command"] == "chrome"), None
        )
        assert chrome_in_broadcast is not None
        assert chrome_in_broadcast["cpu"] == 85.1
        assert chrome_in_broadcast["score"] > 0

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


# --- Integration Test: Score Range Verification ---


# Test cases for score range verification:
# (top_output, min_expected_score, max_expected_score)
# Scoring weights: cpu=25, state=20, pageins=15, mem=15, cmprs=10, csw=10, sysbsd=5
# Normalization: cpu/100, pageins/1000, mem/8GB, cmprs/1GB, csw/100k, sysbsd/100k
SCORE_RANGE_TEST_CASES = [
    pytest.param(
        # Low stress (score < 35)
        # 10% cpu = 2.5
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      idle             10.0 sleeping 100M   0B     1      1000       500        10
""",
        0,
        34,
        id="low-stress",
    ),
    pytest.param(
        # Medium stress (35 <= score < 65 without category bonus)
        # 100% cpu = 25, 500 pageins = 7.5, 4GB mem = 7.5, 100k csw = 10 = ~50 base
        # With multi-category bonus (7 categories): 1.5x multiplier → ~75-85
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      medium           100.0 running 4G     200M   50     100000     50000      500
""",
        70,
        90,
        id="medium-stress",
    ),
    pytest.param(
        # High stress (stuck + high metrics) (score >= 65)
        # 100% cpu = 25, stuck = 20, 1000 pageins = 15, 8GB mem = 15 = 75+
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      stressor         100.0 stuck   8G     1G     200    100000     100000     1000
""",
        65,
        100,
        id="high-stress",
    ),
]


@pytest.mark.parametrize("top_output,min_score,max_score", SCORE_RANGE_TEST_CASES)
@pytest.mark.asyncio
async def test_score_ranges(monkeypatch, top_output, min_score, max_score):
    """Scoring should produce expected ranges for different stress levels."""
    config = Config()
    collector = TopCollector(config)

    async def mock_run_top():
        return top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    # Verify score is in expected range
    assert min_score <= samples.max_score <= max_score, (
        f"Expected score in [{min_score}, {max_score}], got {samples.max_score}"
    )


# --- Integration Test: Empty/Edge Cases ---


@pytest.mark.asyncio
async def test_empty_top_output_handling(monkeypatch):
    """Collector should handle empty top output gracefully."""
    config = Config()
    collector = TopCollector(config)

    async def mock_run_top():
        return ""

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    samples = await collector.collect()

    assert isinstance(samples, ProcessSamples)
    assert samples.process_count == 0
    assert samples.max_score == 0
    assert len(samples.rogues) == 0
