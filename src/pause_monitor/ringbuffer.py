# src/pause_monitor/ringbuffer.py
"""Ring buffer for stress samples and process snapshots.

Stores 30 seconds of history at 100ms resolution (300 samples).
On pause detection, buffer is frozen and included in forensics.
"""

from dataclasses import dataclass
from datetime import datetime

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
