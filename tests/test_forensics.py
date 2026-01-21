"""Tests for forensics capture."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pause_monitor.forensics import (
    ForensicsCapture,
    capture_spindump,
    capture_system_logs,
    capture_tailspin,
    create_event_dir,
    run_full_capture,
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
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=2)
    frozen = buffer.freeze()

    # Create capture with buffer
    capture = ForensicsCapture(event_dir=tmp_path)
    capture.write_ring_buffer(frozen)

    # Verify file exists
    assert (tmp_path / "ring_buffer.json").exists()

    # Verify contents
    data = json.loads((tmp_path / "ring_buffer.json").read_text())
    assert len(data["samples"]) == 2
