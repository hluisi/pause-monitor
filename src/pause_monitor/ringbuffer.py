# src/pause_monitor/ringbuffer.py
"""Ring buffer for process samples.

Stores 30 samples at 100ms resolution (3 seconds of history).
On pause detection, buffer is frozen and included in forensics.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from pause_monitor.collector import ProcessSamples


@dataclass
class RingSample:
    """Single sample in the ring buffer."""

    timestamp: datetime
    samples: ProcessSamples
    tier: int


@dataclass
class BufferContents:
    """Immutable snapshot for forensics."""

    samples: list[RingSample]


class RingBuffer:
    """Ring buffer for process samples.

    Stores up to max_samples (default 30 = 3 seconds at 100ms).
    """

    def __init__(self, max_samples: int = 30) -> None:
        self._samples: deque[RingSample] = deque(maxlen=max_samples)

    @property
    def samples(self) -> list[RingSample]:
        """Read-only access to samples (returns a copy)."""
        return list(self._samples)

    def push(self, samples: ProcessSamples, tier: int) -> None:
        """Add a sample to the buffer."""
        self._samples.append(
            RingSample(
                timestamp=datetime.now(),
                samples=samples,
                tier=tier,
            )
        )

    def freeze(self) -> BufferContents:
        """Return immutable copy of buffer contents."""
        return BufferContents(samples=list(self._samples))
