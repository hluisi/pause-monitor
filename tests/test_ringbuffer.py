# tests/test_ringbuffer.py
"""Tests for ring buffer module."""

from datetime import datetime

from pause_monitor.collector import ProcessSamples
from pause_monitor.ringbuffer import BufferContents, RingBuffer, RingSample


def make_test_samples(**kwargs) -> ProcessSamples:
    """Create ProcessSamples with sensible defaults for testing."""
    defaults = {
        "timestamp": datetime.now(),
        "elapsed_ms": 100,
        "process_count": 50,
        "max_score": 25,
        "rogues": [],
    }
    defaults.update(kwargs)
    return ProcessSamples(**defaults)


class TestRingSample:
    """Tests for RingSample dataclass."""

    def test_creation(self):
        """RingSample stores timestamp, samples, and tier."""
        samples = make_test_samples(max_score=50)
        ring_sample = RingSample(
            timestamp=datetime.now(),
            samples=samples,
            tier=2,
        )
        assert ring_sample.tier == 2
        assert ring_sample.samples.max_score == 50

    def test_preserves_process_data(self):
        """RingSample preserves ProcessSamples data for forensics."""
        samples = make_test_samples(
            elapsed_ms=150,
            process_count=100,
            max_score=75,
        )
        ring_sample = RingSample(
            timestamp=datetime.now(),
            samples=samples,
            tier=3,
        )
        assert ring_sample.samples.elapsed_ms == 150
        assert ring_sample.samples.process_count == 100
        assert ring_sample.samples.max_score == 75


class TestBufferContents:
    """Tests for BufferContents dataclass."""

    def test_creation(self):
        """BufferContents holds a list of RingSamples."""
        samples = make_test_samples()
        ring_sample = RingSample(timestamp=datetime.now(), samples=samples, tier=1)
        contents = BufferContents(samples=[ring_sample])
        assert len(contents.samples) == 1

    def test_empty(self):
        """BufferContents can be empty."""
        contents = BufferContents(samples=[])
        assert len(contents.samples) == 0


class TestRingBuffer:
    """Tests for RingBuffer class."""

    def test_default_max_samples(self):
        """Default max_samples is 30."""
        buffer = RingBuffer()
        # Fill beyond default capacity
        for i in range(35):
            buffer.push(make_test_samples(max_score=i), tier=1)
        assert len(buffer.samples) == 30
        # Oldest were evicted, so first score should be 5 (35-30)
        assert buffer.samples[0].samples.max_score == 5

    def test_push_stores_samples(self):
        """push() stores ProcessSamples in buffer."""
        buffer = RingBuffer(max_samples=10)
        samples = make_test_samples(max_score=42)
        buffer.push(samples, tier=1)

        assert len(buffer.samples) == 1
        assert buffer.samples[0].samples.max_score == 42
        assert buffer.samples[0].tier == 1

    def test_push_evicts_oldest(self):
        """push() evicts oldest when buffer is full."""
        buffer = RingBuffer(max_samples=3)

        buffer.push(make_test_samples(max_score=10), tier=1)
        first_time = buffer.samples[0].timestamp

        buffer.push(make_test_samples(max_score=20), tier=1)
        buffer.push(make_test_samples(max_score=30), tier=1)
        buffer.push(make_test_samples(max_score=40), tier=1)  # Evicts first

        assert len(buffer.samples) == 3
        assert buffer.samples[0].timestamp != first_time
        assert buffer.samples[0].samples.max_score == 20

    def test_samples_returns_copy(self):
        """samples property returns a copy, not the original."""
        buffer = RingBuffer(max_samples=10)
        buffer.push(make_test_samples(), tier=1)

        samples1 = buffer.samples
        samples2 = buffer.samples

        assert samples1 is not samples2  # Different list objects
        assert samples1 == samples2  # But equal content

        # Modifying the returned list doesn't affect buffer
        samples1.clear()
        assert len(buffer.samples) == 1

    def test_freeze_returns_immutable_snapshot(self):
        """freeze() returns BufferContents with copy of samples."""
        buffer = RingBuffer(max_samples=10)
        buffer.push(make_test_samples(max_score=50), tier=1)

        frozen = buffer.freeze()

        # Modifying original doesn't affect frozen
        buffer.push(make_test_samples(max_score=60), tier=2)
        assert len(frozen.samples) == 1
        assert len(buffer.samples) == 2

    def test_freeze_empty_buffer(self):
        """freeze() works on empty buffer."""
        buffer = RingBuffer(max_samples=10)
        frozen = buffer.freeze()
        assert len(frozen.samples) == 0

    def test_size_one_buffer(self):
        """RingBuffer with max_samples=1 only keeps last sample."""
        buffer = RingBuffer(max_samples=1)
        buffer.push(make_test_samples(max_score=10), tier=1)
        buffer.push(make_test_samples(max_score=20), tier=2)

        assert len(buffer.samples) == 1
        assert buffer.samples[0].tier == 2
        assert buffer.samples[0].samples.max_score == 20

    def test_stores_tier(self):
        """push() records the tier with each sample."""
        buffer = RingBuffer(max_samples=10)
        buffer.push(make_test_samples(), tier=1)
        buffer.push(make_test_samples(), tier=2)
        buffer.push(make_test_samples(), tier=3)

        assert buffer.samples[0].tier == 1
        assert buffer.samples[1].tier == 2
        assert buffer.samples[2].tier == 3

    def test_adds_timestamp(self):
        """push() adds a timestamp to each sample."""
        buffer = RingBuffer(max_samples=10)
        before = datetime.now()
        buffer.push(make_test_samples(), tier=1)
        after = datetime.now()

        sample = buffer.samples[0]
        assert before <= sample.timestamp <= after
