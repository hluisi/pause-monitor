# src/pause_monitor/ringbuffer.py
"""Ring buffer for stress samples and process snapshots.

Stores 30 seconds of history at 100ms resolution (300 samples).
On pause detection, buffer is frozen and included in forensics.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime

import psutil

from pause_monitor.stress import StressBreakdown


@dataclass
class ProcessInfo:
    """Process information for snapshots."""

    pid: int
    name: str
    cpu_pct: float
    memory_mb: float


@dataclass
class ProcessSnapshot:
    """Snapshot of top processes at a point in time."""

    timestamp: datetime
    trigger: str  # "tier2_entry", "tier2_peak", "tier2_exit", "tier3_periodic", "pause"
    by_cpu: list[ProcessInfo]  # top 10 by CPU
    by_memory: list[ProcessInfo]  # top 10 by memory


@dataclass
class RingSample:
    """Single stress sample in the ring buffer."""

    timestamp: datetime
    stress: StressBreakdown
    tier: int  # 1, 2, or 3 at time of capture


@dataclass
class BufferContents:
    """Immutable snapshot of ring buffer contents."""

    samples: list[RingSample]
    snapshots: list[ProcessSnapshot]


class RingBuffer:
    """Ring buffer for stress samples with process snapshot support.

    Stores up to max_samples (default 300 = 30 seconds at 100ms).
    Process snapshots are stored separately and cleared on de-escalation.
    """

    def __init__(self, max_samples: int = 300) -> None:
        self._samples: deque[RingSample] = deque(maxlen=max_samples)
        self._snapshots: list[ProcessSnapshot] = []

    @property
    def samples(self) -> deque[RingSample]:
        """Read-only access to samples."""
        return self._samples

    @property
    def snapshots(self) -> list[ProcessSnapshot]:
        """Read-only access to snapshots."""
        return self._snapshots

    def push(self, stress: StressBreakdown, tier: int) -> None:
        """Add a stress sample to the buffer."""
        self._samples.append(
            RingSample(
                timestamp=datetime.now(),
                stress=stress,
                tier=tier,
            )
        )

    def snapshot_processes(self, trigger: str) -> None:
        """Capture current top processes by CPU and memory."""
        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                processes.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info["name"] or "unknown",
                        cpu_pct=info["cpu_percent"] or 0.0,
                        memory_mb=(info["memory_info"].rss if info["memory_info"] else 0)
                        / 1024
                        / 1024,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        by_cpu = sorted(processes, key=lambda p: p.cpu_pct, reverse=True)[:10]
        by_memory = sorted(processes, key=lambda p: p.memory_mb, reverse=True)[:10]

        self._snapshots.append(
            ProcessSnapshot(
                timestamp=datetime.now(),
                trigger=trigger,
                by_cpu=by_cpu,
                by_memory=by_memory,
            )
        )

    def freeze(self) -> BufferContents:
        """Return immutable copy of buffer contents."""
        return BufferContents(
            samples=list(self._samples),
            snapshots=list(self._snapshots),
        )

    def clear_snapshots(self) -> None:
        """Clear process snapshots (called on de-escalation)."""
        self._snapshots.clear()
