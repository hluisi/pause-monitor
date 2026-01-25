"""Integration tests for per-process scoring feature.

Tests the end-to-end data flow:
  TopCollector → ProcessSamples → RingBuffer → Storage → Socket

These tests use mocks where appropriate to avoid running actual system commands.
"""

import asyncio
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.collector import ProcessSamples, ProcessScore, TopCollector
from pause_monitor.config import Config
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.socket_server import SocketServer
from pause_monitor.storage import (
    create_event,
    get_process_samples,
    insert_process_sample,
)

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


# --- Integration Test: TopCollector → ProcessSamples ---


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


# --- Integration Test: ProcessSamples → Storage → Retrieval ---


def test_process_samples_storage_roundtrip(initialized_db):
    """ProcessSamples should be stored and retrieved correctly via JSON blob storage."""
    conn = sqlite3.connect(initialized_db)

    # Create an event
    event_id = create_event(conn, datetime.now())

    # Create ProcessSamples with multiple rogues
    original_samples = ProcessSamples(
        timestamp=datetime(2026, 1, 24, 12, 0, 0),
        elapsed_ms=1050,
        process_count=500,
        max_score=75,
        rogues=[
            ProcessScore(
                pid=7229,
                command="chrome",
                cpu=85.1,
                state="running",
                mem=1024 * 1024 * 1024,  # 1GB
                cmprs=50 * 1024 * 1024,  # 50MB
                pageins=500,
                csw=1134810,
                sysbsd=3961273,
                threads=38,
                score=75,
                categories=frozenset({"cpu", "pageins", "mem"}),
            ),
            ProcessScore(
                pid=0,
                command="kernel_task",
                cpu=18.1,
                state="stuck",
                mem=43 * 1024 * 1024,
                cmprs=0,
                pageins=0,
                csw=793476910,
                sysbsd=0,
                threads=870,
                score=25,
                categories=frozenset({"stuck", "threads"}),
            ),
        ],
    )

    # Store at tier 2
    insert_process_sample(conn, event_id, tier=2, samples=original_samples)

    # Retrieve and verify
    records = get_process_samples(conn, event_id)
    conn.close()

    assert len(records) == 1
    record = records[0]
    assert record.tier == 2
    assert record.event_id == event_id

    retrieved = record.data
    assert retrieved.timestamp == original_samples.timestamp
    assert retrieved.elapsed_ms == 1050
    assert retrieved.process_count == 500
    assert retrieved.max_score == 75

    assert len(retrieved.rogues) == 2

    chrome = next(r for r in retrieved.rogues if r.command == "chrome")
    assert chrome.pid == 7229
    assert chrome.cpu == 85.1
    assert chrome.pageins == 500
    assert chrome.score == 75
    assert "cpu" in chrome.categories

    kernel = next(r for r in retrieved.rogues if r.command == "kernel_task")
    assert kernel.pid == 0
    assert kernel.state == "stuck"
    assert kernel.score == 25
    assert "stuck" in kernel.categories


def test_multiple_tier_samples_per_event(initialized_db):
    """Multiple ProcessSamples at different tiers should be stored correctly."""
    conn = sqlite3.connect(initialized_db)

    event_id = create_event(conn, datetime.now())

    # Store tier 2 peak sample
    tier2_samples = make_test_samples(
        max_score=65,
        rogues=[make_test_process_score(pid=100, command="tier2_proc", score=65)],
    )
    insert_process_sample(conn, event_id, tier=2, samples=tier2_samples)

    # Store multiple tier 3 continuous samples
    for i in range(3):
        tier3_samples = make_test_samples(
            max_score=80 + i,
            rogues=[make_test_process_score(pid=200 + i, command=f"tier3_proc_{i}", score=80 + i)],
        )
        insert_process_sample(conn, event_id, tier=3, samples=tier3_samples)

    records = get_process_samples(conn, event_id)
    conn.close()

    assert len(records) == 4

    tier2_records = [r for r in records if r.tier == 2]
    tier3_records = [r for r in records if r.tier == 3]

    assert len(tier2_records) == 1
    assert len(tier3_records) == 3

    assert tier2_records[0].data.rogues[0].command == "tier2_proc"
    assert all(r.data.rogues[0].command.startswith("tier3_proc_") for r in tier3_records)


# --- Integration Test: ProcessSamples → RingBuffer → Freeze ---


def test_ring_buffer_stores_process_samples():
    """RingBuffer should store ProcessSamples and maintain order."""
    buffer = RingBuffer(max_samples=10)

    # Push samples with increasing scores
    for i in range(5):
        samples = make_test_samples(
            max_score=10 + i * 10,
            rogues=[make_test_process_score(pid=i, score=10 + i * 10)],
        )
        buffer.push(samples, tier=1)

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
    buffer.push(samples, tier=2)

    frozen = buffer.freeze()

    # Verify frozen state
    assert len(frozen.samples) == 1
    assert frozen.samples[0].tier == 2
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
        buffer.push(samples, tier=1)

    assert len(buffer) == 3

    # Should have the last 3 samples (indices 2, 3, 4)
    ring_samples = buffer.samples
    scores = [rs.samples.max_score for rs in ring_samples]
    assert scores == [20, 30, 40]


# --- Integration Test: Full Cycle with Socket ---


@pytest.mark.asyncio
async def test_full_collection_to_socket_cycle(monkeypatch, short_tmp_path, sample_top_output):
    """Test complete collection → buffer → socket broadcast cycle."""
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
        tier = 2 if samples.max_score >= 35 else 1
        buffer.push(samples, tier)

        # Broadcast to clients
        await server.broadcast(samples, tier)

        # Verify client receives broadcast
        broadcast_data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        broadcast_msg = json.loads(broadcast_data.decode())

        assert broadcast_msg["type"] == "sample"
        assert broadcast_msg["max_score"] == samples.max_score
        assert broadcast_msg["tier"] == tier
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


@pytest.mark.asyncio
async def test_collection_storage_retrieval_cycle(monkeypatch, initialized_db, sample_top_output):
    """Test collection → storage → retrieval cycle."""
    conn = sqlite3.connect(initialized_db)

    config = Config()
    collector = TopCollector(config)

    async def mock_run_top():
        return sample_top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    # Create event
    event_id = create_event(conn, datetime.now())

    # Collect samples
    samples = await collector.collect()

    # Determine tier based on max_score (thresholds: 35/65)
    if samples.max_score >= 65:
        tier = 3
    elif samples.max_score >= 35:
        tier = 2
    else:
        tier = 1

    # Store (only tier 2+ would normally be stored)
    if tier >= 2:
        insert_process_sample(conn, event_id, tier=tier, samples=samples)

    # Retrieve
    records = get_process_samples(conn, event_id)
    conn.close()

    if tier >= 2:
        assert len(records) == 1
        retrieved = records[0].data

        # Verify data integrity
        assert retrieved.max_score == samples.max_score
        assert retrieved.process_count == samples.process_count
        assert len(retrieved.rogues) == len(samples.rogues)

        # Verify rogues match
        for orig, retr in zip(samples.rogues, retrieved.rogues):
            assert orig.pid == retr.pid
            assert orig.command == retr.command
            assert orig.score == retr.score
            assert orig.categories == retr.categories
    else:
        # Tier 1 samples are not stored
        assert len(records) == 0


# --- Integration Test: Tier Determination ---


# Test cases for tier determination:
# (top_output, min_expected_score, max_expected_score, expected_tier)
# Scoring weights: cpu=25, state=20, pageins=15, mem=15, cmprs=10, csw=10, sysbsd=5
# Normalization: cpu/100, pageins/1000, mem/8GB, cmprs/1GB, csw/100k, sysbsd/100k
TIER_TEST_CASES = [
    pytest.param(
        # Low stress - should be tier 1 (score < 35)
        # 10% cpu = 2.5
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      idle             10.0 sleeping 100M   0B     1      1000       500        10
""",
        0,
        34,
        1,
        id="tier1-low-stress",
    ),
    pytest.param(
        # Medium stress - should be tier 2 (35 <= score < 65)
        # 100% cpu = 25, 500 pageins = 7.5, 4GB mem = 7.5, 100k csw = 10 = ~50
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      medium           100.0 running 4G     200M   50     100000     50000      500
""",
        35,
        64,
        2,
        id="tier2-medium-stress",
    ),
    pytest.param(
        # High stress (stuck + high metrics) - should be tier 3 (score >= 65)
        # 100% cpu = 25, stuck = 20, 1000 pageins = 15, 8GB mem = 15 = 75+
        """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      stressor         100.0 stuck   8G     1G     200    100000     100000     1000
""",
        65,
        100,
        3,
        id="tier3-high-stress",
    ),
]


@pytest.mark.parametrize("top_output,min_score,max_score,expected_tier", TIER_TEST_CASES)
@pytest.mark.asyncio
async def test_tier_determination_from_max_score(
    monkeypatch, top_output, min_score, max_score, expected_tier
):
    """Tier should be determined by max_score thresholds (35/65)."""
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

    # Determine tier based on thresholds
    if samples.max_score >= 65:
        actual_tier = 3
    elif samples.max_score >= 35:
        actual_tier = 2
    else:
        actual_tier = 1

    assert actual_tier == expected_tier, (
        f"Expected tier {expected_tier} for max_score={samples.max_score}, got tier {actual_tier}"
    )


# --- Integration Test: Process Categories Tracking ---


@pytest.mark.asyncio
async def test_process_categories_preserved_through_cycle(monkeypatch, initialized_db):
    """Process categories should be preserved through collection → storage → retrieval."""
    conn = sqlite3.connect(initialized_db)

    config = Config()
    collector = TopCollector(config)

    # Output with process triggering multiple categories
    top_output = """
PID    COMMAND          %CPU STATE    MEM    CMPRS  #TH    CSW        SYSBSD     PAGEINS
1      multi_stress     90.0 running  4G     100M   500    50000      25000      800
"""

    async def mock_run_top():
        return top_output

    monkeypatch.setattr(collector, "_run_top", mock_run_top)

    # Collect
    samples = await collector.collect()
    assert len(samples.rogues) >= 1

    rogue = samples.rogues[0]
    original_categories = rogue.categories

    # Should have multiple categories
    assert len(original_categories) > 1, f"Expected multiple categories, got {original_categories}"
    assert "cpu" in original_categories  # 90% CPU

    # Store and retrieve
    event_id = create_event(conn, datetime.now())
    insert_process_sample(conn, event_id, tier=2, samples=samples)

    records = get_process_samples(conn, event_id)
    conn.close()

    retrieved_rogue = records[0].data.rogues[0]
    assert retrieved_rogue.categories == original_categories


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


def test_empty_process_samples_storage(initialized_db):
    """Empty ProcessSamples should be stored and retrieved correctly."""
    conn = sqlite3.connect(initialized_db)

    event_id = create_event(conn, datetime.now())

    empty_samples = ProcessSamples(
        timestamp=datetime.now(),
        elapsed_ms=1000,
        process_count=0,
        max_score=0,
        rogues=[],
    )

    insert_process_sample(conn, event_id, tier=1, samples=empty_samples)

    records = get_process_samples(conn, event_id)
    conn.close()

    assert len(records) == 1
    assert records[0].data.process_count == 0
    assert records[0].data.max_score == 0
    assert len(records[0].data.rogues) == 0
