"""Tests for forensics capture."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.collector import PowermetricsResult
from pause_monitor.forensics import (
    ForensicsCapture,
    capture_spindump,
    capture_system_logs,
    capture_tailspin,
    create_event_dir,
    run_full_capture,
)


def make_test_metrics(**kwargs) -> PowermetricsResult:
    """Create PowermetricsResult with sensible defaults for testing."""
    defaults = {
        "elapsed_ns": 100_000_000,
        "throttled": False,
        "cpu_power": 5.0,
        "gpu_pct": 10.0,
        "gpu_power": 1.0,
        "io_read_per_s": 1000.0,
        "io_write_per_s": 500.0,
        "wakeups_per_s": 50.0,
        "pageins_per_s": 0.0,
        "top_cpu_processes": [],
        "top_pagein_processes": [],
    }
    defaults.update(kwargs)
    return PowermetricsResult(**defaults)


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
    """run_full_capture runs all capture steps."""
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

                mock_spin.assert_called_once_with(event_dir)
                mock_tail.assert_called_once_with(event_dir)
                mock_logs.assert_called_once_with(event_dir, window_seconds=60)


def test_forensics_capture_includes_ring_buffer(tmp_path):
    """ForensicsCapture writes ring buffer contents to event dir."""
    import json

    from pause_monitor.forensics import ForensicsCapture
    from pause_monitor.ringbuffer import RingBuffer
    from pause_monitor.stress import StressBreakdown

    # Create buffer with samples
    buffer = RingBuffer(max_samples=10)
    metrics = make_test_metrics()
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(metrics, stress, tier=1)
    buffer.push(metrics, stress, tier=2)
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
    assert "stress" in sample
    assert "tier" in sample
    assert sample["tier"] == 1
    assert sample["stress"]["load"] == 10

    # Verify snapshots key exists (empty in this test)
    assert "snapshots" in data
    assert data["snapshots"] == []


def test_forensics_capture_ring_buffer_with_snapshots(tmp_path):
    """ForensicsCapture correctly serializes process snapshots."""
    import json

    from pause_monitor.forensics import ForensicsCapture
    from pause_monitor.ringbuffer import ProcessInfo, ProcessSnapshot, RingBuffer
    from pause_monitor.stress import StressBreakdown

    # Create buffer with samples and a snapshot
    buffer = RingBuffer(max_samples=10)
    metrics = make_test_metrics()
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(metrics, stress, tier=1)
    buffer.push(metrics, stress, tier=2)

    # Manually add a snapshot to test serialization
    from datetime import datetime

    snapshot = ProcessSnapshot(
        timestamp=datetime.now(),
        trigger="tier2_entry",
        by_cpu=[
            ProcessInfo(pid=123, name="chrome", cpu_pct=45.0, memory_mb=512.0),
            ProcessInfo(pid=456, name="python", cpu_pct=30.0, memory_mb=256.0),
        ],
        by_memory=[
            ProcessInfo(pid=123, name="chrome", cpu_pct=45.0, memory_mb=512.0),
            ProcessInfo(pid=789, name="vscode", cpu_pct=5.0, memory_mb=1024.0),
        ],
    )
    buffer._snapshots.append(snapshot)
    frozen = buffer.freeze()

    # Create capture and write
    capture = ForensicsCapture(event_dir=tmp_path)
    capture.write_ring_buffer(frozen)

    # Verify contents
    data = json.loads((tmp_path / "ring_buffer.json").read_text())

    # Verify samples structure
    assert len(data["samples"]) == 2
    sample = data["samples"][0]
    assert "timestamp" in sample
    assert "stress" in sample
    assert "tier" in sample
    assert sample["tier"] == 1
    assert sample["stress"]["load"] == 10

    # Verify snapshots structure
    assert len(data["snapshots"]) == 1
    snap = data["snapshots"][0]
    assert "timestamp" in snap
    assert snap["trigger"] == "tier2_entry"
    assert len(snap["by_cpu"]) == 2
    assert len(snap["by_memory"]) == 2
    assert snap["by_cpu"][0]["name"] == "chrome"
    assert snap["by_cpu"][0]["cpu_pct"] == 45.0


def test_identify_culprits_from_buffer():
    """identify_culprits correlates high stress factors with processes."""
    from datetime import datetime

    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, ProcessInfo, ProcessSnapshot, RingSample
    from pause_monitor.stress import StressBreakdown

    # High memory stress
    metrics = make_test_metrics()
    samples = [
        RingSample(
            timestamp=datetime.now(),
            metrics=metrics,
            stress=StressBreakdown(load=5, memory=25, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
            tier=2,
        )
    ]

    # Process snapshot with memory hog
    snapshots = [
        ProcessSnapshot(
            timestamp=datetime.now(),
            trigger="tier2_entry",
            by_cpu=[],
            by_memory=[ProcessInfo(pid=1, name="Chrome", cpu_pct=10, memory_mb=2048)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    assert len(culprits) == 1
    assert culprits[0]["factor"] == "memory"
    assert "Chrome" in culprits[0]["processes"]


def test_identify_culprits_multiple_factors():
    """identify_culprits returns multiple factors sorted by score."""
    from datetime import datetime

    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, ProcessInfo, ProcessSnapshot, RingSample
    from pause_monitor.stress import StressBreakdown

    # High load and memory stress
    metrics = make_test_metrics()
    samples = [
        RingSample(
            timestamp=datetime.now(),
            metrics=metrics,
            stress=StressBreakdown(
                load=30, memory=15, thermal=0, latency=0, io=0, gpu=0, wakeups=0
            ),
            tier=2,
        )
    ]

    snapshots = [
        ProcessSnapshot(
            timestamp=datetime.now(),
            trigger="tier2_entry",
            by_cpu=[ProcessInfo(pid=1, name="python", cpu_pct=150, memory_mb=500)],
            by_memory=[ProcessInfo(pid=2, name="Chrome", cpu_pct=10, memory_mb=2048)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    # Should have both factors, sorted by score (load=30 > memory=15)
    assert len(culprits) == 2
    assert culprits[0]["factor"] == "load"
    assert culprits[0]["score"] == 30
    assert culprits[1]["factor"] == "memory"
    assert culprits[1]["score"] == 15


def test_identify_culprits_empty_buffer():
    """identify_culprits returns empty list for empty buffer."""
    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents

    contents = BufferContents(samples=[], snapshots=[])
    culprits = identify_culprits(contents)

    assert culprits == []


def test_identify_culprits_averages_samples():
    """identify_culprits averages stress over all samples."""
    from datetime import datetime, timedelta

    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, ProcessInfo, ProcessSnapshot, RingSample
    from pause_monitor.stress import StressBreakdown

    now = datetime.now()
    metrics = make_test_metrics()
    # Three samples with memory stress: 20, 10, 0 -> average 10
    samples = [
        RingSample(
            timestamp=now - timedelta(seconds=2),
            metrics=metrics,
            stress=StressBreakdown(load=0, memory=20, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
            tier=2,
        ),
        RingSample(
            timestamp=now - timedelta(seconds=1),
            metrics=metrics,
            stress=StressBreakdown(load=0, memory=10, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
            tier=2,
        ),
        RingSample(
            timestamp=now,
            metrics=metrics,
            stress=StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
            tier=1,
        ),
    ]

    snapshots = [
        ProcessSnapshot(
            timestamp=now,
            trigger="tier2_entry",
            by_cpu=[],
            by_memory=[ProcessInfo(pid=1, name="Safari", cpu_pct=5, memory_mb=1024)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    # Average memory is 10, which is threshold
    assert len(culprits) == 1
    assert culprits[0]["factor"] == "memory"
    assert culprits[0]["score"] == 10


def test_identify_culprits_below_threshold():
    """identify_culprits returns empty when all factors below threshold."""
    from datetime import datetime

    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, ProcessInfo, ProcessSnapshot, RingSample
    from pause_monitor.stress import StressBreakdown

    # All factors below threshold of 10
    metrics = make_test_metrics()
    samples = [
        RingSample(
            timestamp=datetime.now(),
            metrics=metrics,
            stress=StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=5, gpu=5, wakeups=5),
            tier=1,
        )
    ]

    snapshots = [
        ProcessSnapshot(
            timestamp=datetime.now(),
            trigger="tier2_entry",
            by_cpu=[ProcessInfo(pid=1, name="python", cpu_pct=50, memory_mb=500)],
            by_memory=[ProcessInfo(pid=1, name="python", cpu_pct=50, memory_mb=500)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    assert culprits == []


def test_identify_culprits_gpu_factor():
    """identify_culprits correctly identifies high GPU stress."""
    from datetime import datetime

    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, ProcessInfo, ProcessSnapshot, RingSample
    from pause_monitor.stress import StressBreakdown

    # High GPU stress (above threshold of 10)
    metrics = make_test_metrics()
    samples = [
        RingSample(
            timestamp=datetime.now(),
            metrics=metrics,
            stress=StressBreakdown(load=5, memory=5, thermal=0, latency=0, io=0, gpu=15, wakeups=0),
            tier=2,
        )
    ]

    snapshots = [
        ProcessSnapshot(
            timestamp=datetime.now(),
            trigger="tier2_entry",
            by_cpu=[ProcessInfo(pid=1, name="blender", cpu_pct=200, memory_mb=4096)],
            by_memory=[ProcessInfo(pid=1, name="blender", cpu_pct=200, memory_mb=4096)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    # Should identify GPU factor with CPU processes (GPU-heavy processes typically high CPU)
    assert len(culprits) == 1
    assert culprits[0]["factor"] == "gpu"
    assert culprits[0]["score"] == 15
    assert "blender" in culprits[0]["processes"]
