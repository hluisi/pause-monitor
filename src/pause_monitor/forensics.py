"""Forensics capture for pause events."""

import asyncio
import json
from dataclasses import asdict
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
                    "timestamp": s.timestamp.isoformat(),
                    "stress": asdict(s.stress),
                    "tier": s.tier,
                }
                for s in contents.samples
            ],
            "snapshots": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "trigger": s.trigger,
                    "by_cpu": [asdict(p) for p in s.by_cpu],
                    "by_memory": [asdict(p) for p in s.by_memory],
                }
                for s in contents.snapshots
            ],
        }

        path = self.event_dir / "ring_buffer.json"
        path.write_text(json.dumps(data, indent=2))
        log.debug("ring_buffer_written", path=str(path), samples=len(contents.samples))


def identify_culprits(contents: "BufferContents") -> list[dict]:
    """Identify culprits by correlating stress factors with process data.

    Uses stress scores to identify WHICH factors are elevated (the "what"),
    then maps those factors to the relevant processes (the "who").

    Args:
        contents: Frozen ring buffer contents with samples and snapshots

    Returns:
        List of {"factor": str, "score": int, "processes": [str]}
        sorted by score descending
    """
    if not contents.samples:
        return []

    # Find MAX stress for each factor across all samples (spikes are transient)
    max_stress: dict[str, float] = {
        "load": 0,
        "memory": 0,
        "thermal": 0,
        "latency": 0,
        "io": 0,
        "gpu": 0,
        "wakeups": 0,
        "pageins": 0,
    }

    for sample in contents.samples:
        if sample.stress:
            max_stress["load"] = max(max_stress["load"], sample.stress.load)
            max_stress["memory"] = max(max_stress["memory"], sample.stress.memory)
            max_stress["thermal"] = max(max_stress["thermal"], sample.stress.thermal)
            max_stress["latency"] = max(max_stress["latency"], sample.stress.latency)
            max_stress["io"] = max(max_stress["io"], sample.stress.io)
            max_stress["gpu"] = max(max_stress["gpu"], sample.stress.gpu)
            max_stress["wakeups"] = max(max_stress["wakeups"], sample.stress.wakeups)
            max_stress["pageins"] = max(max_stress["pageins"], sample.stress.pageins)

    # Collect processes from snapshots (for load/memory factors)
    cpu_processes: list[str] = []
    memory_processes: list[str] = []
    for snapshot in contents.snapshots:
        for proc in snapshot.by_cpu:
            if proc.name not in cpu_processes:
                cpu_processes.append(proc.name)
        for proc in snapshot.by_memory:
            if proc.name not in memory_processes:
                memory_processes.append(proc.name)

    # Collect per-process data from metrics (for pagein/wakeup/io factors)
    pagein_processes: list[str] = []
    wakeup_processes: list[str] = []
    diskio_processes: list[str] = []

    for sample in contents.samples:
        if not sample.metrics:
            continue

        # Top pagein processes
        if sample.metrics.top_pagein_processes:
            for proc in sample.metrics.top_pagein_processes:
                name = proc.get("name", "?")
                if name not in pagein_processes:
                    pagein_processes.append(name)

        # Top wakeup processes
        if sample.metrics.top_wakeup_processes:
            for proc in sample.metrics.top_wakeup_processes:
                name = proc.get("name", "?")
                if name not in wakeup_processes:
                    wakeup_processes.append(name)

        # Top disk I/O processes
        if sample.metrics.top_diskio_processes:
            for proc in sample.metrics.top_diskio_processes:
                name = proc.get("name", "?")
                if name not in diskio_processes:
                    diskio_processes.append(name)

    # Map factors to their relevant process source
    factor_to_processes = {
        "load": cpu_processes,
        "memory": memory_processes,
        "thermal": cpu_processes,  # Thermal stress from CPU-heavy processes
        "latency": cpu_processes,  # Latency often from CPU contention
        "io": diskio_processes if diskio_processes else cpu_processes,
        "gpu": cpu_processes,  # GPU stress from compute-heavy processes
        "wakeups": wakeup_processes if wakeup_processes else cpu_processes,
        "pageins": pagein_processes if pagein_processes else cpu_processes,
    }

    # Build culprits list for factors above threshold
    threshold = 10  # Minimum score to be considered a culprit
    culprits = []

    for factor, score in max_stress.items():
        if score >= threshold:
            processes = factor_to_processes.get(factor, [])[:5]
            culprits.append(
                {
                    "factor": factor,
                    "score": int(score),
                    "processes": processes,
                }
            )

    return sorted(culprits, key=lambda c: c["score"], reverse=True)


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
