"""Tests for forensics capture."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.collector import ProcessSamples, ProcessScore
from pause_monitor.forensics import (
    ForensicsCapture,
    capture_spindump,
    capture_system_logs,
    capture_tailspin,
    create_event_dir,
    identify_culprits,
    run_full_capture,
)
from pause_monitor.ringbuffer import BufferContents, RingBuffer, RingSample


def make_process_score(
    pid: int = 1,
    command: str = "test",
    cpu: float = 50.0,
    score: int = 25,
    categories: frozenset[str] | None = None,
    **kwargs,
) -> ProcessScore:
    """Create ProcessScore with sensible defaults for testing."""
    defaults = {
        "state": "running",
        "mem": 100 * 1024 * 1024,  # 100MB
        "cmprs": 10 * 1024 * 1024,  # 10MB
        "pageins": 100,
        "csw": 1000,
        "sysbsd": 500,
        "threads": 10,
    }
    defaults.update(kwargs)
    import time

    return ProcessScore(
        pid=pid,
        command=command,
        cpu=cpu,
        state=defaults["state"],
        mem=defaults["mem"],
        cmprs=defaults["cmprs"],
        pageins=defaults["pageins"],
        csw=defaults["csw"],
        sysbsd=defaults["sysbsd"],
        threads=defaults["threads"],
        score=score,
        categories=categories or frozenset({"cpu"}),
        captured_at=defaults.get("captured_at", time.time()),
    )


def make_process_samples(
    rogues: list[ProcessScore] | None = None,
    max_score: int | None = None,
    process_count: int = 100,
    elapsed_ms: int = 50,
    timestamp: datetime | None = None,
) -> ProcessSamples:
    """Create ProcessSamples with sensible defaults for testing."""
    if rogues is None:
        rogues = []
    if max_score is None:
        max_score = max((r.score for r in rogues), default=0)
    return ProcessSamples(
        timestamp=timestamp or datetime.now(),
        elapsed_ms=elapsed_ms,
        process_count=process_count,
        max_score=max_score,
        rogues=rogues,
    )


def test_create_event_dir(tmp_path: Path):
    """create_event_dir creates timestamped directory."""
    events_dir = tmp_path / "events"
    event_time = datetime(2024, 1, 15, 10, 30, 45)

    event_dir = create_event_dir(events_dir, event_time)

    assert event_dir.exists()
    assert "2024-01-15" in event_dir.name
    assert "10-30-45" in event_dir.name


def test_forensics_capture_creates_files(tmp_path: Path):
    """ForensicsCapture creates expected files."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    # Write test data
    capture.write_metadata({"timestamp": 1705323045, "duration": 3.5})

    assert (event_dir / "metadata.json").exists()


def test_forensics_capture_writes_process_snapshot(tmp_path: Path):
    """ForensicsCapture writes process snapshot."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    processes = [
        {"pid": 123, "name": "codemeter", "cpu": 50.0},
        {"pid": 456, "name": "python", "cpu": 10.0},
    ]
    capture.write_process_snapshot(processes)

    assert (event_dir / "processes.json").exists()


@pytest.mark.asyncio
async def test_capture_spindump_creates_file(tmp_path: Path):
    """capture_spindump creates spindump output file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"spindump output", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        success = await capture_spindump(event_dir)

        assert success is True
        # Verify spindump was called
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "spindump" in call_args[0]


@pytest.mark.asyncio
async def test_capture_spindump_writes_output(tmp_path: Path):
    """capture_spindump writes stdout to file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"spindump output data", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        await capture_spindump(event_dir)

        output_path = event_dir / "spindump.txt"
        assert output_path.exists()
        assert output_path.read_bytes() == b"spindump output data"


@pytest.mark.asyncio
async def test_capture_spindump_timeout(tmp_path: Path):
    """capture_spindump returns False on timeout."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.side_effect = asyncio.TimeoutError()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()
        mock_exec.return_value = mock_process

        success = await capture_spindump(event_dir, timeout=1.0)

        assert success is False
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_tailspin_creates_file(tmp_path: Path):
    """capture_tailspin creates tailspin output file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_process

        # Create the output file as tailspin would
        (event_dir / "tailspin.tailspin").write_bytes(b"tailspin data")

        success = await capture_tailspin(event_dir)

        assert success is True
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "tailspin" in call_args[0]


@pytest.mark.asyncio
async def test_capture_tailspin_timeout(tmp_path: Path):
    """capture_tailspin returns False on timeout."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        # First wait() raises TimeoutError, second wait() (cleanup) succeeds
        mock_process.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), None])
        mock_process.kill = MagicMock()
        mock_exec.return_value = mock_process

        success = await capture_tailspin(event_dir, timeout=1.0)

        assert success is False
        mock_process.kill.assert_called_once()
        assert mock_process.wait.await_count == 2


@pytest.mark.asyncio
async def test_capture_system_logs_creates_file(tmp_path: Path):
    """capture_system_logs creates filtered log file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"log output here", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        success = await capture_system_logs(event_dir, window_seconds=60)

        assert success is True
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "log" in call_args[0]


@pytest.mark.asyncio
async def test_capture_system_logs_writes_output(tmp_path: Path):
    """capture_system_logs writes stdout to file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"filtered log data", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        await capture_system_logs(event_dir, window_seconds=30)

        output_path = event_dir / "system.log"
        assert output_path.exists()
        assert output_path.read_bytes() == b"filtered log data"


@pytest.mark.asyncio
async def test_capture_system_logs_timeout(tmp_path: Path):
    """capture_system_logs returns False on timeout."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.side_effect = asyncio.TimeoutError()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()
        mock_exec.return_value = mock_process

        success = await capture_system_logs(event_dir, timeout=1.0)

        assert success is False
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_full_capture_orchestrates_all(tmp_path: Path):
    """run_full_capture runs all capture steps with default timeouts."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    with patch("pause_monitor.forensics.capture_spindump") as mock_spin:
        with patch("pause_monitor.forensics.capture_tailspin") as mock_tail:
            with patch("pause_monitor.forensics.capture_system_logs") as mock_logs:
                mock_spin.return_value = True
                mock_tail.return_value = True
                mock_logs.return_value = True

                await run_full_capture(capture, window_seconds=60)

                # Default timeouts: spindump=30, tailspin=10, logs=10
                mock_spin.assert_called_once_with(event_dir, timeout=30)
                mock_tail.assert_called_once_with(event_dir, timeout=10)
                mock_logs.assert_called_once_with(event_dir, window_seconds=60, timeout=10)


@pytest.mark.asyncio
async def test_run_full_capture_uses_config_timeouts(tmp_path: Path):
    """run_full_capture uses timeouts from ForensicsConfig."""
    from pause_monitor.config import ForensicsConfig

    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)
    config = ForensicsConfig(spindump_timeout=45, tailspin_timeout=15, logs_timeout=20)

    with patch("pause_monitor.forensics.capture_spindump") as mock_spin:
        with patch("pause_monitor.forensics.capture_tailspin") as mock_tail:
            with patch("pause_monitor.forensics.capture_system_logs") as mock_logs:
                mock_spin.return_value = True
                mock_tail.return_value = True
                mock_logs.return_value = True

                await run_full_capture(capture, window_seconds=60, config=config)

                # Custom timeouts from config
                mock_spin.assert_called_once_with(event_dir, timeout=45)
                mock_tail.assert_called_once_with(event_dir, timeout=15)
                mock_logs.assert_called_once_with(event_dir, window_seconds=60, timeout=20)


def test_forensics_capture_includes_ring_buffer(tmp_path):
    """ForensicsCapture writes ring buffer contents to event dir."""
    # Create buffer with samples
    buffer = RingBuffer(max_samples=10)
    rogue = make_process_score(command="chrome", score=25, categories=frozenset({"cpu", "mem"}))
    samples1 = make_process_samples(rogues=[rogue], max_score=25)
    samples2 = make_process_samples(rogues=[rogue], max_score=25)
    buffer.push(samples1)
    buffer.push(samples2)
    frozen = buffer.freeze()

    # Create capture with buffer
    capture = ForensicsCapture(event_dir=tmp_path)
    capture.write_ring_buffer(frozen)

    # Verify file exists
    assert (tmp_path / "ring_buffer.json").exists()

    # Verify contents
    data = json.loads((tmp_path / "ring_buffer.json").read_text())
    assert len(data["samples"]) == 2

    # Verify sample structure
    sample = data["samples"][0]
    assert "timestamp" in sample
    assert "max_score" in sample
    assert "process_count" in sample
    assert "rogues" in sample
    assert sample["max_score"] == 25

    # Verify rogues structure
    assert len(sample["rogues"]) == 1
    rogue_data = sample["rogues"][0]
    assert rogue_data["command"] == "chrome"
    assert rogue_data["score"] == 25
    assert set(rogue_data["categories"]) == {"cpu", "mem"}


def test_forensics_capture_ring_buffer_with_multiple_rogues(tmp_path):
    """ForensicsCapture correctly serializes multiple rogues."""
    # Create buffer with samples containing multiple rogues
    buffer = RingBuffer(max_samples=10)
    rogues = [
        make_process_score(pid=123, command="chrome", cpu=45.0, score=30),
        make_process_score(pid=456, command="python", cpu=30.0, score=20),
    ]
    samples = make_process_samples(rogues=rogues, max_score=30)
    buffer.push(samples)
    frozen = buffer.freeze()

    # Create capture and write
    capture = ForensicsCapture(event_dir=tmp_path)
    capture.write_ring_buffer(frozen)

    # Verify contents
    data = json.loads((tmp_path / "ring_buffer.json").read_text())

    # Verify samples structure
    assert len(data["samples"]) == 1
    sample = data["samples"][0]
    assert sample["max_score"] == 30

    # Verify rogues
    assert len(sample["rogues"]) == 2
    assert sample["rogues"][0]["command"] == "chrome"
    assert sample["rogues"][0]["cpu"] == 45.0
    assert sample["rogues"][1]["command"] == "python"


def test_identify_culprits_from_buffer():
    """identify_culprits returns top rogues by score."""
    rogue = make_process_score(
        pid=100, command="Chrome", score=30, categories=frozenset({"cpu", "mem"})
    )
    samples = make_process_samples(rogues=[rogue])
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    assert len(culprits) == 1
    assert culprits[0]["pid"] == 100
    assert culprits[0]["command"] == "Chrome"
    assert culprits[0]["score"] == 30
    assert set(culprits[0]["categories"]) == {"cpu", "mem"}


def test_identify_culprits_multiple_processes():
    """identify_culprits returns multiple processes sorted by score."""
    rogues = [
        make_process_score(pid=100, command="python", score=30),
        make_process_score(pid=200, command="Chrome", score=15),
    ]
    samples = make_process_samples(rogues=rogues)
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    # Should have both processes, sorted by score (python=30 > Chrome=15)
    assert len(culprits) == 2
    assert culprits[0]["pid"] == 100
    assert culprits[0]["command"] == "python"
    assert culprits[0]["score"] == 30
    assert culprits[1]["pid"] == 200
    assert culprits[1]["command"] == "Chrome"
    assert culprits[1]["score"] == 15


def test_identify_culprits_empty_buffer():
    """identify_culprits returns empty list for empty buffer."""
    contents = BufferContents(samples=())

    culprits = identify_culprits(contents)

    assert culprits == []


def test_identify_culprits_uses_peak_values():
    """identify_culprits uses MAX (peak) score across samples.

    If a process appears in multiple samples with different scores,
    the peak score should be used.
    """
    now = datetime.now()
    # Same process (same PID) with different scores across samples
    samples = [
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=20)],
            timestamp=now - timedelta(seconds=2),
        ),
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=35)],  # Peak
            timestamp=now - timedelta(seconds=1),
        ),
        make_process_samples(
            rogues=[make_process_score(pid=300, command="Safari", score=10)],
            timestamp=now,
        ),
    ]
    ring_samples = tuple(RingSample(samples=s) for s in samples)
    contents = BufferContents(samples=ring_samples)

    culprits = identify_culprits(contents)

    # Should use peak score of 35
    assert len(culprits) == 1
    assert culprits[0]["pid"] == 300
    assert culprits[0]["command"] == "Safari"
    assert culprits[0]["score"] == 35


def test_identify_culprits_returns_all_sorted_by_score():
    """identify_culprits returns all rogues sorted by score descending."""
    rogues = [make_process_score(pid=i, command=f"proc{i}", score=100 - i) for i in range(10)]
    samples = make_process_samples(rogues=rogues)
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    # All rogues returned, sorted by score descending
    assert len(culprits) == 10
    assert culprits[0]["pid"] == 0
    assert culprits[0]["score"] == 100
    assert culprits[9]["pid"] == 9
    assert culprits[9]["score"] == 91


def test_identify_culprits_no_rogues():
    """identify_culprits handles samples with no rogues."""
    samples = make_process_samples(rogues=[])
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    assert culprits == []


def test_identify_culprits_differentiates_by_pid():
    """identify_culprits treats processes with same command but different PIDs as separate.

    Two Chrome processes with different PIDs should appear as separate entries,
    not be merged together.
    """
    rogues = [
        make_process_score(pid=1001, command="Chrome", score=40),
        make_process_score(pid=1002, command="Chrome", score=25),
    ]
    samples = make_process_samples(rogues=rogues)
    ring_sample = RingSample(samples=samples)
    contents = BufferContents(samples=(ring_sample,))

    culprits = identify_culprits(contents)

    # Should have both Chrome processes as separate entries
    assert len(culprits) == 2
    # Sorted by score descending
    assert culprits[0]["pid"] == 1001
    assert culprits[0]["command"] == "Chrome"
    assert culprits[0]["score"] == 40
    assert culprits[1]["pid"] == 1002
    assert culprits[1]["command"] == "Chrome"
    assert culprits[1]["score"] == 25


def test_identify_culprits_peak_by_pid_not_command():
    """Peak scoring is per-PID, not per-command.

    If two Chrome processes exist, each should have its own peak score tracked.
    """
    now = datetime.now()
    samples = [
        make_process_samples(
            rogues=[
                make_process_score(pid=1001, command="Chrome", score=30),
                make_process_score(pid=1002, command="Chrome", score=20),
            ],
            timestamp=now - timedelta(seconds=1),
        ),
        make_process_samples(
            rogues=[
                make_process_score(pid=1001, command="Chrome", score=25),  # Lower than peak
                make_process_score(pid=1002, command="Chrome", score=35),  # New peak for 1002
            ],
            timestamp=now,
        ),
    ]
    ring_samples = tuple(RingSample(samples=s) for s in samples)
    contents = BufferContents(samples=ring_samples)

    culprits = identify_culprits(contents)

    # Should have two separate Chrome entries with their respective peaks
    assert len(culprits) == 2
    # pid=1002 has higher peak (35), so it should be first
    assert culprits[0]["pid"] == 1002
    assert culprits[0]["command"] == "Chrome"
    assert culprits[0]["score"] == 35
    # pid=1001 has peak of 30
    assert culprits[1]["pid"] == 1001
    assert culprits[1]["command"] == "Chrome"
    assert culprits[1]["score"] == 30
