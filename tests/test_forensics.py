"""Tests for forensics capture."""

from datetime import datetime
from pathlib import Path

from pause_monitor.forensics import ForensicsCapture, create_event_dir


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
