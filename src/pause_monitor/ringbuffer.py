# src/pause_monitor/ringbuffer.py
"""Ring buffer for stress samples and process snapshots.

Stores 30 seconds of history at 100ms resolution (300 samples).
On pause detection, buffer is frozen and included in forensics.
"""

from dataclasses import dataclass
from datetime import datetime

from pause_monitor.stress import StressBreakdown


@dataclass
class RingSample:
    """Single stress sample in the ring buffer."""

    timestamp: datetime
    stress: StressBreakdown
    tier: int  # 1, 2, or 3 at time of capture
