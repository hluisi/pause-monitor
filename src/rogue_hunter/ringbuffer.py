# src/rogue_hunter/ringbuffer.py
"""Ring buffer for process samples.

Stores 30 samples at 1Hz resolution (30 seconds of history).
On pause detection, buffer is frozen and included in forensics.
"""

from collections import deque
from dataclasses import dataclass

from rogue_hunter.collector import ProcessSamples


@dataclass
class RingSample:
    """Single sample in the ring buffer.

    Timestamp is accessed via samples.timestamp (not duplicated here).
    """

    samples: ProcessSamples


@dataclass(frozen=True)
class BufferContents:
    """Immutable snapshot for forensics."""

    samples: tuple[RingSample, ...]


class RingBuffer:
    """Ring buffer for process samples.

    Stores up to max_samples (default 30 = 3 seconds at 100ms).
    """

    def __init__(self, max_samples: int = 30) -> None:
        self._samples: deque[RingSample] = deque(maxlen=max_samples)

    def __len__(self) -> int:
        """Return number of samples in buffer."""
        return len(self._samples)

    @property
    def is_empty(self) -> bool:
        """Return True if buffer has no samples."""
        return len(self._samples) == 0

    @property
    def capacity(self) -> int:
        """Return maximum number of samples the buffer can hold."""
        return self._samples.maxlen or 0

    @property
    def samples(self) -> list[RingSample]:
        """Read-only access to samples (returns a copy)."""
        return list(self._samples)

    def push(self, samples: ProcessSamples) -> None:
        """Add a sample to the buffer."""
        self._samples.append(RingSample(samples=samples))

    def clear(self) -> None:
        """Empty the buffer."""
        self._samples.clear()

    def freeze(self) -> BufferContents:
        """Return immutable copy of buffer contents."""
        return BufferContents(samples=tuple(self._samples))
