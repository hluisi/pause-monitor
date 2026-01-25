"""Forensics capture for pause events."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pause_monitor.ringbuffer import BufferContents

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

    def write_ring_buffer(self, contents: "BufferContents") -> None:
        """Write ring buffer contents to event directory."""
        data = {
            "samples": [
                {
                    "timestamp": s.samples.timestamp.isoformat(),
                    "tier": s.tier,
                    "max_score": s.samples.max_score,
                    "process_count": s.samples.process_count,
                    "rogues": [p.to_dict() for p in s.samples.rogues],
                }
                for s in contents.samples
            ]
        }

        path = self.event_dir / "ring_buffer.json"
        path.write_text(json.dumps(data, indent=2))
        log.debug("ring_buffer_written", path=str(path), samples=len(contents.samples))


def identify_culprits(contents: "BufferContents") -> list[dict]:
    """Identify top culprit processes from ring buffer samples.

    With per-process scoring, rogues are already identified and scored.
    This function aggregates across all samples to find the peak offenders.

    Args:
        contents: Frozen ring buffer contents with samples

    Returns:
        List of {"command": str, "score": int, "categories": [str]}
        sorted by score descending, limited to top 5
    """
    if not contents.samples:
        return []

    # Track max score per process (keyed by command name)
    # Processes can appear in multiple samples; we want peak score
    peak_scores: dict[str, dict] = {}

    for sample in contents.samples:
        for rogue in sample.samples.rogues:
            existing = peak_scores.get(rogue.command)
            if existing is None or rogue.score > existing["score"]:
                peak_scores[rogue.command] = {
                    "command": rogue.command,
                    "score": rogue.score,
                    "categories": list(rogue.categories),
                }

    # Sort by score descending and return top 5
    culprits = sorted(peak_scores.values(), key=lambda c: c["score"], reverse=True)
    return culprits[:5]


async def capture_spindump(event_dir: Path, timeout: float = 30.0) -> bool:
    """Capture thread stacks via spindump.

    Args:
        event_dir: Directory to write spindump output
        timeout: Maximum seconds to wait for spindump

    Returns:
        True if capture succeeded
    """
    output_path = event_dir / "spindump.txt"

    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/spindump",
            "-notarget",
            "-stdout",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        output_path.write_bytes(stdout)
        log.info("spindump_captured", path=str(output_path), size=len(stdout))
        return True

    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        log.warning("spindump_timeout", timeout=timeout)
        return False
    except (FileNotFoundError, PermissionError) as e:
        log.warning("spindump_failed", error=str(e))
        return False


async def capture_tailspin(event_dir: Path, timeout: float = 10.0) -> bool:
    """Capture kernel trace via tailspin.

    Args:
        event_dir: Directory to write tailspin output
        timeout: Maximum seconds to wait

    Returns:
        True if capture succeeded
    """
    output_path = event_dir / "tailspin.tailspin"

    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/tailspin",
            "save",
            "-o",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await asyncio.wait_for(process.wait(), timeout=timeout)

        if output_path.exists():
            log.info("tailspin_captured", path=str(output_path))
            return True
        return False

    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        log.warning("tailspin_timeout", timeout=timeout)
        return False
    except (FileNotFoundError, PermissionError) as e:
        log.warning("tailspin_failed", error=str(e))
        return False


async def capture_system_logs(
    event_dir: Path,
    window_seconds: int = 60,
    timeout: float = 10.0,
) -> bool:
    """Capture filtered system logs around the event.

    Args:
        event_dir: Directory to write log output
        window_seconds: Seconds of logs to capture before event
        timeout: Maximum seconds to wait

    Returns:
        True if capture succeeded
    """
    output_path = event_dir / "system.log"

    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/log",
            "show",
            "--last",
            f"{window_seconds}s",
            "--predicate",
            'subsystem == "com.apple.powerd" OR '
            'subsystem == "com.apple.kernel" OR '
            'subsystem == "com.apple.windowserver" OR '
            'eventMessage CONTAINS[c] "hang" OR '
            'eventMessage CONTAINS[c] "stall" OR '
            'eventMessage CONTAINS[c] "timeout"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        output_path.write_bytes(stdout)
        log.info("logs_captured", path=str(output_path), size=len(stdout))
        return True

    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        log.warning("logs_timeout", timeout=timeout)
        return False
    except (FileNotFoundError, PermissionError) as e:
        log.warning("logs_failed", error=str(e))
        return False


async def run_full_capture(
    capture: ForensicsCapture,
    window_seconds: int = 60,
) -> None:
    """Run all forensic capture steps.

    Args:
        capture: ForensicsCapture instance with event_dir set
        window_seconds: Seconds of history to capture
    """
    # Run captures concurrently
    await asyncio.gather(
        capture_spindump(capture.event_dir),
        capture_tailspin(capture.event_dir),
        capture_system_logs(capture.event_dir, window_seconds=window_seconds),
    )

    log.info("full_capture_complete", event_dir=str(capture.event_dir))
