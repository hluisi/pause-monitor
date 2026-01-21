"""Forensics capture for pause events."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


def create_event_dir(events_dir: Path, event_time: datetime) -> Path:
    """Create directory for a pause event.

    Args:
        events_dir: Parent directory for all events
        event_time: Timestamp of the event

    Returns:
        Path to the created event directory
    """
    events_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = event_time.strftime("%Y-%m-%d_%H-%M-%S")
    event_dir = events_dir / timestamp_str

    # Handle duplicates by appending counter
    counter = 0
    while event_dir.exists():
        counter += 1
        event_dir = events_dir / f"{timestamp_str}_{counter}"

    event_dir.mkdir()
    log.info("event_dir_created", path=str(event_dir))
    return event_dir


class ForensicsCapture:
    """Captures forensic data for a pause event."""

    def __init__(self, event_dir: Path):
        self.event_dir = event_dir

    def write_metadata(self, metadata: dict[str, Any]) -> None:
        """Write event metadata to JSON file."""
        path = self.event_dir / "metadata.json"
        path.write_text(json.dumps(metadata, indent=2))

    def write_process_snapshot(self, processes: list[dict[str, Any]]) -> None:
        """Write process snapshot to JSON file."""
        path = self.event_dir / "processes.json"
        path.write_text(json.dumps(processes, indent=2))

    def write_text_artifact(self, name: str, content: str) -> None:
        """Write a text artifact file."""
        path = self.event_dir / name
        path.write_text(content)

    def write_binary_artifact(self, name: str, content: bytes) -> None:
        """Write a binary artifact file."""
        path = self.event_dir / name
        path.write_bytes(content)
