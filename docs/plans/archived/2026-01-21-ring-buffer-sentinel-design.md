# Ring Buffer Sentinel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace adaptive sampling with a continuous 10Hz stress sentinel using a ring buffer for 30-second pre-incident history, enabling tiered escalation and culprit identification.

**Architecture:** Always-on lightweight stress polling (100ms fast loop + 1s slow loop) stores samples in a ring buffer. Stress thresholds (15/50) trigger tier escalation with process snapshots. On pause detection, the ring buffer is frozen and included in forensics.

**Tech Stack:** Python 3.14, asyncio, collections.deque, psutil, ctypes (sysctl), SQLite

---

## Phase 1: Foundation - Stress Model Expansion

### Task 1: Expand StressBreakdown with GPU and Wakeups Fields

**Files:**
- Modify: `src/pause_monitor/stress.py:53-69`
- Test: `tests/test_stress.py`

**Step 1: Write the failing test for new fields**

```python
# Add to tests/test_stress.py

def test_stress_breakdown_has_all_factors():
    """Verify StressBreakdown has all 7 factors."""
    breakdown = StressBreakdown(
        load=10, memory=15, thermal=0, latency=5, io=10, gpu=8, wakeups=5
    )
    assert breakdown.load == 10
    assert breakdown.memory == 15
    assert breakdown.thermal == 0
    assert breakdown.latency == 5
    assert breakdown.io == 10
    assert breakdown.gpu == 8
    assert breakdown.wakeups == 5


def test_stress_breakdown_total_includes_all_factors():
    """Verify total sums all 7 factors."""
    breakdown = StressBreakdown(
        load=40, memory=30, thermal=20, latency=30, io=20, gpu=20, wakeups=20
    )
    # 40+30+20+30+20+20+20 = 180, capped at 100
    assert breakdown.total == 100


def test_stress_breakdown_total_uncapped():
    """Verify total with small values."""
    breakdown = StressBreakdown(
        load=5, memory=5, thermal=0, latency=0, io=0, gpu=3, wakeups=2
    )
    assert breakdown.total == 15
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stress.py::test_stress_breakdown_has_all_factors -v`
Expected: FAIL with "TypeError: StressBreakdown.__init__() got unexpected keyword arguments 'gpu', 'wakeups'"

**Step 3: Update StressBreakdown dataclass**

```python
@dataclass
class StressBreakdown:
    """Per-factor stress scores.

    This is the CANONICAL definition - storage.py imports from here.
    """

    load: int  # 0-40: load/cores ratio
    memory: int  # 0-30: memory pressure
    thermal: int  # 0-20: throttling active
    latency: int  # 0-30: self-latency
    io: int  # 0-20: disk I/O spike
    gpu: int  # 0-20: GPU usage sustained high
    wakeups: int  # 0-20: idle wakeups sustained high

    @property
    def total(self) -> int:
        """Combined stress score, capped at 100."""
        return min(
            100,
            self.load + self.memory + self.thermal + self.latency + self.io + self.gpu + self.wakeups,
        )
```

**Step 4: Run tests to verify new tests pass**

Run: `uv run pytest tests/test_stress.py -v`
Expected: FAIL - existing tests will break due to missing gpu/wakeups args

**Step 5: Fix existing tests by adding default values**

Update all existing `StressBreakdown()` calls in tests to include `gpu=0, wakeups=0`:

```python
# test_stress_breakdown_total
StressBreakdown(load=10, memory=15, thermal=20, latency=5, io=10, gpu=0, wakeups=0)

# test_stress_breakdown_total_capped
StressBreakdown(load=40, memory=30, thermal=20, latency=30, io=20, gpu=0, wakeups=0)

# etc. for all test fixtures
```

**Step 6: Run all stress tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "$(cat <<'EOF'
feat(stress): add gpu and wakeups factors to StressBreakdown

Expands the stress model from 5 to 7 factors in preparation for
the sentinel architecture. GPU and wakeups scoring will be
implemented in the next task.
EOF
)"
```

---

### Task 2: Update calculate_stress for GPU and Wakeups

**Files:**
- Modify: `src/pause_monitor/stress.py:114-164`
- Test: `tests/test_stress.py`

**Step 1: Write failing tests for new parameters**

```python
def test_stress_gpu_contribution():
    """GPU stress when sustained above 80%."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=85.0,
        wakeups_per_sec=100,
    )
    assert breakdown.gpu == 20  # Above 80% threshold


def test_stress_gpu_below_threshold():
    """No GPU stress below 80%."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=70.0,
        wakeups_per_sec=100,
    )
    assert breakdown.gpu == 0


def test_stress_wakeups_contribution():
    """Wakeups stress when above 1000/sec."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=0.0,
        wakeups_per_sec=1500,
    )
    assert breakdown.wakeups == 20  # Above 1000/sec threshold


def test_stress_wakeups_below_threshold():
    """No wakeups stress below 1000/sec."""
    breakdown = calculate_stress(
        load_avg=1.0,
        core_count=4,
        mem_available_pct=50.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=0,
        gpu_pct=0.0,
        wakeups_per_sec=500,
    )
    assert breakdown.wakeups == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stress.py::test_stress_gpu_contribution -v`
Expected: FAIL with "TypeError: calculate_stress() got unexpected keyword arguments"

**Step 3: Update calculate_stress signature and implementation**

```python
def calculate_stress(
    load_avg: float,
    core_count: int,
    mem_available_pct: float,
    throttled: bool | None,
    latency_ratio: float,
    io_rate: int,
    io_baseline: int,
    gpu_pct: float | None = None,
    wakeups_per_sec: int | None = None,
) -> StressBreakdown:
    """Calculate stress score from current system metrics.

    Args:
        load_avg: 1-minute load average
        core_count: Number of CPU cores
        mem_available_pct: Percentage of memory available (0-100)
        throttled: True if thermal throttling active, None if unknown
        latency_ratio: actual_interval / expected_interval
        io_rate: Current I/O bytes/sec (read + write)
        io_baseline: Baseline I/O bytes/sec (EMA)
        gpu_pct: GPU utilization percentage (0-100), None if unknown
        wakeups_per_sec: Idle wakeups per second, None if unknown

    Returns:
        StressBreakdown with per-factor and total scores
    """
    # Load average relative to cores (max 40 points)
    load_ratio = load_avg / core_count if core_count > 0 else 0
    load_score = min(40, max(0, int((load_ratio - 1.0) * 20)))

    # Memory pressure (max 30 points)
    mem_score = min(30, max(0, int((20 - mem_available_pct) * 1.5)))

    # Thermal throttling (20 points if active)
    thermal_score = 20 if throttled else 0

    # Self-latency (max 30 points, only if ratio > 1.5)
    if latency_ratio > 1.5:
        latency_score = min(30, max(0, int((latency_ratio - 1.0) * 20)))
    else:
        latency_score = 0

    # Disk I/O spike (20 points if detected)
    spike_detected = io_baseline > 0 and io_rate > io_baseline * 10
    sustained_high = io_rate > 100_000_000  # 100 MB/s
    io_score = 20 if (spike_detected or sustained_high) else 0

    # GPU usage (20 points if sustained above 80%)
    gpu_score = 20 if gpu_pct is not None and gpu_pct > 80.0 else 0

    # Idle wakeups (20 points if above 1000/sec)
    wakeups_score = 20 if wakeups_per_sec is not None and wakeups_per_sec > 1000 else 0

    return StressBreakdown(
        load=load_score,
        memory=mem_score,
        thermal=thermal_score,
        latency=latency_score,
        io=io_score,
        gpu=gpu_score,
        wakeups=wakeups_score,
    )
```

**Step 4: Run new tests**

Run: `uv run pytest tests/test_stress.py::test_stress_gpu_contribution tests/test_stress.py::test_stress_gpu_below_threshold tests/test_stress.py::test_stress_wakeups_contribution tests/test_stress.py::test_stress_wakeups_below_threshold -v`
Expected: PASS

**Step 5: Update existing calculate_stress tests**

Existing tests use old signature. Add default values to preserve behavior:

```python
# For test_stress_zero_when_idle and similar:
breakdown = calculate_stress(
    load_avg=1.0,
    core_count=4,
    mem_available_pct=50.0,
    throttled=False,
    latency_ratio=1.0,
    io_rate=0,
    io_baseline=0,
    # gpu_pct and wakeups_per_sec default to None (no contribution)
)
```

**Step 6: Run all stress tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "$(cat <<'EOF'
feat(stress): add GPU and wakeups scoring to calculate_stress

GPU contributes 20 points when sustained above 80%.
Idle wakeups contribute 20 points when above 1000/sec.
Both parameters are optional with None defaults for backwards
compatibility with existing daemon code.
EOF
)"
```

---

### Task 3: Update Daemon to Pass GPU/Wakeups to calculate_stress

**Files:**
- Modify: `src/pause_monitor/daemon.py` (in `_collect_sample`)
- Test: `tests/test_daemon.py`

**Step 1: Find where calculate_stress is called in daemon**

Read `_collect_sample()` method to understand current call.

**Step 2: Write integration test**

```python
# In tests/test_daemon.py

@pytest.mark.asyncio
async def test_daemon_passes_gpu_to_stress_calculation(mock_powermetrics, mock_db):
    """Verify daemon extracts and passes GPU percentage to stress calculation."""
    # Create daemon with mock dependencies
    daemon = Daemon(config, mock_db)

    # Mock powermetrics to return GPU data
    mock_powermetrics.read_samples.return_value = [
        PowermetricsResult(
            cpu_pct=50.0,
            gpu_pct=85.0,  # Above 80% threshold
            # ... other fields
        )
    ]

    # Collect sample
    await daemon._collect_sample(mock_powermetrics.read_samples()[0])

    # Verify stress includes gpu contribution
    # (Check via last_sample or storage insert call)
```

**Step 3: Update _collect_sample to extract and pass GPU**

In `daemon.py`, update the `_collect_sample` method to pass `gpu_pct` from `PowermetricsResult` to `calculate_stress()`.

**Step 4: Run daemon tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "$(cat <<'EOF'
feat(daemon): pass GPU percentage to stress calculation

Extracts gpu_pct from powermetrics samples and includes it
in stress scoring. Idle wakeups will be added when
powermetrics parsing is expanded.
EOF
)"
```

---

## Phase 2: Ring Buffer Implementation

### Task 4: Create Ring Buffer Module with RingSample

**Files:**
- Create: `src/pause_monitor/ringbuffer.py`
- Create: `tests/test_ringbuffer.py`

**Step 1: Write failing test for RingSample dataclass**

```python
# tests/test_ringbuffer.py

from datetime import datetime
from pause_monitor.ringbuffer import RingSample, RingBuffer
from pause_monitor.stress import StressBreakdown


def test_ring_sample_creation():
    """RingSample stores timestamp, stress breakdown, and tier."""
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    sample = RingSample(
        timestamp=datetime.now(),
        stress=stress,
        tier=1,
    )
    assert sample.tier == 1
    assert sample.stress.total == 15
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ringbuffer.py::test_ring_sample_creation -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'pause_monitor.ringbuffer'"

**Step 3: Create ringbuffer.py with RingSample**

```python
# src/pause_monitor/ringbuffer.py
"""Ring buffer for stress samples and process snapshots.

Stores 30 seconds of history at 100ms resolution (300 samples).
On pause detection, buffer is frozen and included in forensics.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from pause_monitor.stress import StressBreakdown


@dataclass
class RingSample:
    """Single stress sample in the ring buffer."""

    timestamp: datetime
    stress: StressBreakdown
    tier: int  # 1, 2, or 3 at time of capture
```

**Step 4: Run test**

Run: `uv run pytest tests/test_ringbuffer.py::test_ring_sample_creation -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/ringbuffer.py tests/test_ringbuffer.py
git commit -m "$(cat <<'EOF'
feat(ringbuffer): add RingSample dataclass

Foundation for ring buffer storage. Captures timestamp,
stress breakdown, and monitoring tier at time of sample.
EOF
)"
```

---

### Task 5: Add ProcessInfo and ProcessSnapshot

**Files:**
- Modify: `src/pause_monitor/ringbuffer.py`
- Test: `tests/test_ringbuffer.py`

**Step 1: Write failing tests**

```python
def test_process_info_creation():
    """ProcessInfo stores process details."""
    from pause_monitor.ringbuffer import ProcessInfo

    info = ProcessInfo(pid=1234, name="Chrome", cpu_pct=45.5, memory_mb=2048.0)
    assert info.pid == 1234
    assert info.name == "Chrome"
    assert info.cpu_pct == 45.5
    assert info.memory_mb == 2048.0


def test_process_snapshot_creation():
    """ProcessSnapshot stores top processes with trigger reason."""
    from pause_monitor.ringbuffer import ProcessInfo, ProcessSnapshot

    by_cpu = [ProcessInfo(pid=1, name="Proc1", cpu_pct=50.0, memory_mb=100.0)]
    by_memory = [ProcessInfo(pid=2, name="Proc2", cpu_pct=10.0, memory_mb=2000.0)]

    snapshot = ProcessSnapshot(
        timestamp=datetime.now(),
        trigger="tier2_entry",
        by_cpu=by_cpu,
        by_memory=by_memory,
    )
    assert snapshot.trigger == "tier2_entry"
    assert len(snapshot.by_cpu) == 1
    assert len(snapshot.by_memory) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ringbuffer.py::test_process_info_creation -v`
Expected: FAIL with "ImportError: cannot import name 'ProcessInfo'"

**Step 3: Add dataclasses**

```python
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_ringbuffer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/ringbuffer.py tests/test_ringbuffer.py
git commit -m "$(cat <<'EOF'
feat(ringbuffer): add ProcessInfo and ProcessSnapshot dataclasses

ProcessInfo captures per-process metrics (pid, name, cpu, memory).
ProcessSnapshot groups top-10 by CPU and memory with trigger reason.
EOF
)"
```

---

### Task 6: Implement RingBuffer Class

**Files:**
- Modify: `src/pause_monitor/ringbuffer.py`
- Test: `tests/test_ringbuffer.py`

**Step 1: Write failing tests for RingBuffer**

```python
def test_ring_buffer_push():
    """RingBuffer stores samples up to max size."""
    from pause_monitor.ringbuffer import RingBuffer

    buffer = RingBuffer(max_samples=3)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)

    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)

    assert len(buffer.samples) == 3


def test_ring_buffer_evicts_oldest():
    """RingBuffer evicts oldest when full."""
    buffer = RingBuffer(max_samples=3)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)

    buffer.push(stress, tier=1)  # Will be evicted
    first_time = buffer.samples[0].timestamp

    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=1)  # Evicts first

    assert len(buffer.samples) == 3
    assert buffer.samples[0].timestamp != first_time


def test_ring_buffer_snapshot_processes():
    """RingBuffer captures process snapshots."""
    buffer = RingBuffer(max_samples=300)

    # Mock psutil.process_iter or pass process list
    buffer.snapshot_processes(trigger="tier2_entry")

    assert len(buffer.snapshots) == 1
    assert buffer.snapshots[0].trigger == "tier2_entry"


def test_ring_buffer_freeze():
    """freeze() returns immutable copy of buffer contents."""
    buffer = RingBuffer(max_samples=300)
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.snapshot_processes(trigger="test")

    frozen = buffer.freeze()

    # Modifying original doesn't affect frozen
    buffer.push(stress, tier=2)
    assert len(frozen.samples) == 1
    assert len(buffer.samples) == 2


def test_ring_buffer_clear_snapshots():
    """clear_snapshots() removes process snapshots but keeps samples."""
    buffer = RingBuffer(max_samples=300)
    stress = StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.snapshot_processes(trigger="test")

    buffer.clear_snapshots()

    assert len(buffer.samples) == 1
    assert len(buffer.snapshots) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ringbuffer.py::test_ring_buffer_push -v`
Expected: FAIL with "ImportError: cannot import name 'RingBuffer'"

**Step 3: Implement RingBuffer class**

```python
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
        import psutil

        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                processes.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info["name"] or "unknown",
                        cpu_pct=info["cpu_percent"] or 0.0,
                        memory_mb=(info["memory_info"].rss if info["memory_info"] else 0) / 1024 / 1024,
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_ringbuffer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/ringbuffer.py tests/test_ringbuffer.py
git commit -m "$(cat <<'EOF'
feat(ringbuffer): implement RingBuffer class

- push() adds samples with auto-eviction when full
- snapshot_processes() captures top 10 by CPU/memory via psutil
- freeze() returns immutable copy for forensics
- clear_snapshots() called on tier de-escalation
EOF
)"
```

---

## Phase 3: Sentinel Implementation

### Task 7: Create Sentinel Module with Fast Stress Calculation

**Files:**
- Create: `src/pause_monitor/sentinel.py`
- Create: `tests/test_sentinel.py`

**Step 1: Write failing test for fast stress collection**

```python
# tests/test_sentinel.py

import pytest
from pause_monitor.sentinel import collect_fast_metrics


def test_collect_fast_metrics_returns_dict():
    """Fast metrics collection returns required fields."""
    metrics = collect_fast_metrics()

    assert "load_avg" in metrics
    assert "memory_pressure" in metrics
    assert "page_free_count" in metrics
    assert isinstance(metrics["load_avg"], float)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sentinel.py::test_collect_fast_metrics_returns_dict -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement fast metrics collection**

```python
# src/pause_monitor/sentinel.py
"""Stress sentinel with tiered monitoring.

Fast loop (100ms): load, memory, I/O via sysctl/IOKit
Slow loop (1s): GPU, wakeups, thermal via powermetrics
"""

import ctypes
import os
from ctypes import c_int, c_int64, c_uint, c_size_t, byref

# sysctl interface
libc = ctypes.CDLL(None)
libc.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(c_size_t), ctypes.c_void_p, c_size_t]
libc.sysctlbyname.restype = c_int


def _sysctl_int(name: str) -> int | None:
    """Read an integer sysctl value."""
    value = c_int64()
    size = c_size_t(ctypes.sizeof(value))
    result = libc.sysctlbyname(name.encode(), byref(value), byref(size), None, 0)
    return value.value if result == 0 else None


def collect_fast_metrics() -> dict:
    """Collect fast-path metrics (~20µs).

    Uses sysctl and os.getloadavg() - no subprocess calls.
    """
    load_avg = os.getloadavg()[0]  # 1-minute average
    memory_pressure = _sysctl_int("kern.memorystatus_level")  # 0-100
    page_free_count = _sysctl_int("vm.page_free_count")

    return {
        "load_avg": load_avg,
        "memory_pressure": memory_pressure,
        "page_free_count": page_free_count,
    }
```

**Step 4: Run test**

Run: `uv run pytest tests/test_sentinel.py::test_collect_fast_metrics_returns_dict -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/test_sentinel.py
git commit -m "$(cat <<'EOF'
feat(sentinel): add fast metrics collection via sysctl

collect_fast_metrics() returns load average and memory pressure
using ctypes sysctl calls (~20µs overhead). Foundation for
10Hz sentinel loop.
EOF
)"
```

---

### Task 8: Implement Tier State Machine

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Test: `tests/test_sentinel.py`

**Step 1: Write failing tests for tier transitions**

```python
from pause_monitor.sentinel import TierManager


def test_tier_manager_starts_at_tier1():
    """TierManager starts in Tier 1 (Sentinel)."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    assert manager.current_tier == 1


def test_tier_manager_escalates_to_tier2():
    """Stress >= 15 triggers escalation to Tier 2."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)

    action = manager.update(stress_total=20)

    assert manager.current_tier == 2
    assert action == "tier2_entry"


def test_tier_manager_escalates_to_tier3():
    """Stress >= 50 triggers escalation to Tier 3."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Enter tier 2 first

    action = manager.update(stress_total=55)

    assert manager.current_tier == 3
    assert action == "tier3_entry"


def test_tier_manager_deescalates_with_hysteresis():
    """Tier 2 requires 5 seconds below threshold to de-escalate."""
    import time
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)  # Enter tier 2

    # Still in tier 2 even though stress dropped
    action = manager.update(stress_total=10)
    assert manager.current_tier == 2
    assert action is None

    # Simulate time passing (would need mocking in real test)
    manager._tier2_low_since = time.monotonic() - 6.0
    action = manager.update(stress_total=10)

    assert manager.current_tier == 1
    assert action == "tier2_exit"


def test_tier_manager_peak_tracking():
    """TierManager tracks peak stress during elevated state."""
    manager = TierManager(elevated_threshold=15, critical_threshold=50)
    manager.update(stress_total=20)
    manager.update(stress_total=35)
    manager.update(stress_total=25)

    assert manager.peak_stress == 35
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sentinel.py::test_tier_manager_starts_at_tier1 -v`
Expected: FAIL with "ImportError"

**Step 3: Implement TierManager**

```python
import time
from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    SENTINEL = 1
    ELEVATED = 2
    CRITICAL = 3


class TierManager:
    """Manages tier transitions with hysteresis.

    Tier 1 (Sentinel): stress < elevated_threshold
    Tier 2 (Elevated): elevated_threshold <= stress < critical_threshold
    Tier 3 (Critical): stress >= critical_threshold

    De-escalation requires stress below threshold for 5 seconds.
    """

    def __init__(
        self,
        elevated_threshold: int = 15,
        critical_threshold: int = 50,
        deescalation_delay: float = 5.0,
    ) -> None:
        self.elevated_threshold = elevated_threshold
        self.critical_threshold = critical_threshold
        self.deescalation_delay = deescalation_delay

        self._current_tier = Tier.SENTINEL
        self._tier2_entry_time: float | None = None
        self._tier3_entry_time: float | None = None
        self._tier2_low_since: float | None = None
        self._tier3_low_since: float | None = None
        self._peak_stress = 0

    @property
    def current_tier(self) -> int:
        return int(self._current_tier)

    @property
    def peak_stress(self) -> int:
        return self._peak_stress

    def update(self, stress_total: int) -> str | None:
        """Update tier state based on current stress.

        Returns action string if state change occurred:
        - "tier2_entry", "tier2_exit"
        - "tier3_entry", "tier3_exit"
        - "tier2_peak" if new peak reached in tier 2+
        - None if no action needed
        """
        now = time.monotonic()
        action: str | None = None

        # Track peak during elevated states
        if self._current_tier >= Tier.ELEVATED and stress_total > self._peak_stress:
            self._peak_stress = stress_total
            if self._current_tier == Tier.ELEVATED:
                action = "tier2_peak"

        # Check for escalation
        if stress_total >= self.critical_threshold and self._current_tier < Tier.CRITICAL:
            self._current_tier = Tier.CRITICAL
            self._tier3_entry_time = now
            self._tier3_low_since = None
            return "tier3_entry"

        if stress_total >= self.elevated_threshold and self._current_tier < Tier.ELEVATED:
            self._current_tier = Tier.ELEVATED
            self._tier2_entry_time = now
            self._tier2_low_since = None
            self._peak_stress = stress_total
            return "tier2_entry"

        # Check for de-escalation with hysteresis
        if self._current_tier == Tier.CRITICAL:
            if stress_total < self.critical_threshold:
                if self._tier3_low_since is None:
                    self._tier3_low_since = now
                elif now - self._tier3_low_since >= self.deescalation_delay:
                    self._current_tier = Tier.ELEVATED
                    self._tier3_entry_time = None
                    self._tier3_low_since = None
                    return "tier3_exit"
            else:
                self._tier3_low_since = None

        if self._current_tier == Tier.ELEVATED:
            if stress_total < self.elevated_threshold:
                if self._tier2_low_since is None:
                    self._tier2_low_since = now
                elif now - self._tier2_low_since >= self.deescalation_delay:
                    self._current_tier = Tier.SENTINEL
                    self._tier2_entry_time = None
                    self._tier2_low_since = None
                    self._peak_stress = 0
                    return "tier2_exit"
            else:
                self._tier2_low_since = None

        return action
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_sentinel.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/test_sentinel.py
git commit -m "$(cat <<'EOF'
feat(sentinel): implement TierManager state machine

Manages tier 1/2/3 transitions based on stress thresholds.
- Tier 2 at stress >= 15
- Tier 3 at stress >= 50
- De-escalation requires 5s below threshold (hysteresis)
- Tracks peak stress during elevated states
EOF
)"
```

---

### Task 9: Implement Sentinel Main Loop

**Files:**
- Modify: `src/pause_monitor/sentinel.py`
- Test: `tests/test_sentinel.py`

**Step 1: Write integration test for sentinel loop**

```python
import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from pause_monitor.sentinel import Sentinel
from pause_monitor.ringbuffer import RingBuffer


@pytest.mark.asyncio
async def test_sentinel_fast_loop_pushes_to_buffer():
    """Sentinel fast loop pushes samples to ring buffer."""
    buffer = RingBuffer(max_samples=10)
    sentinel = Sentinel(buffer=buffer, fast_interval_ms=100)

    # Run for 250ms (should get 2-3 samples)
    task = asyncio.create_task(sentinel.start())
    await asyncio.sleep(0.25)
    sentinel.stop()
    await task

    assert len(buffer.samples) >= 2


@pytest.mark.asyncio
async def test_sentinel_triggers_snapshot_on_tier2_entry():
    """Sentinel triggers process snapshot on tier 2 entry."""
    buffer = RingBuffer(max_samples=10)
    sentinel = Sentinel(buffer=buffer, fast_interval_ms=100)

    # Mock high stress to trigger tier 2
    with patch.object(sentinel, '_calculate_fast_stress', return_value=25):
        task = asyncio.create_task(sentinel.start())
        await asyncio.sleep(0.15)  # One sample
        sentinel.stop()
        await task

    # Should have triggered snapshot
    assert len(buffer.snapshots) >= 1
    assert buffer.snapshots[0].trigger == "tier2_entry"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sentinel.py::test_sentinel_fast_loop_pushes_to_buffer -v`
Expected: FAIL with "ImportError: cannot import name 'Sentinel'"

**Step 3: Implement Sentinel class**

```python
import asyncio
import structlog

from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.stress import StressBreakdown, calculate_stress
from pause_monitor.collector import get_core_count

log = structlog.get_logger()


class Sentinel:
    """Continuous stress monitoring sentinel.

    Fast loop (100ms): load, memory via sysctl
    Slow loop (1s): GPU, wakeups, thermal via powermetrics cache
    """

    def __init__(
        self,
        buffer: RingBuffer,
        fast_interval_ms: int = 100,
        slow_interval_ms: int = 1000,
        elevated_threshold: int = 15,
        critical_threshold: int = 50,
    ) -> None:
        self.buffer = buffer
        self.fast_interval = fast_interval_ms / 1000.0
        self.slow_interval = slow_interval_ms / 1000.0
        self.tier_manager = TierManager(elevated_threshold, critical_threshold)

        self._running = False
        self._core_count = get_core_count()

        # Cached slow metrics (updated by slow loop)
        self._cached_gpu_pct: float | None = None
        self._cached_wakeups: int | None = None
        self._cached_throttled: bool | None = None

        # Callbacks
        self.on_tier_change: callable | None = None
        self.on_pause_detected: callable | None = None

    def stop(self) -> None:
        """Signal sentinel to stop."""
        self._running = False

    async def start(self) -> None:
        """Run the sentinel loops."""
        self._running = True

        # Start both loops concurrently
        await asyncio.gather(
            self._fast_loop(),
            self._slow_loop(),
            return_exceptions=True,
        )

    async def _fast_loop(self) -> None:
        """100ms stress sampling loop."""
        last_time = time.monotonic()

        while self._running:
            now = time.monotonic()
            elapsed = now - last_time
            last_time = now

            # Calculate latency ratio
            latency_ratio = elapsed / self.fast_interval if self.fast_interval > 0 else 1.0

            # Collect fast metrics
            metrics = collect_fast_metrics()

            # Calculate stress
            stress = self._calculate_fast_stress(metrics, latency_ratio)

            # Push to ring buffer
            self.buffer.push(stress, tier=self.tier_manager.current_tier)

            # Update tier manager
            action = self.tier_manager.update(stress.total)
            if action:
                await self._handle_tier_action(action)

            # Check for pause (latency ratio > 2.0 indicates missed samples)
            if latency_ratio > 2.0:
                await self._handle_potential_pause(elapsed, self.fast_interval)

            # Sleep for next interval
            await asyncio.sleep(self.fast_interval)

    async def _slow_loop(self) -> None:
        """1s loop for expensive metrics (GPU, wakeups)."""
        # Implementation: spawn lightweight powermetrics for 1 sample
        # Cache results for fast loop to use
        while self._running:
            # TODO: Collect GPU/wakeups/thermal via powermetrics
            await asyncio.sleep(self.slow_interval)

    def _calculate_fast_stress(self, metrics: dict, latency_ratio: float) -> StressBreakdown:
        """Calculate stress from fast metrics + cached slow metrics."""
        # Memory available percentage from pressure level
        mem_pressure = metrics.get("memory_pressure") or 100
        mem_available_pct = float(mem_pressure)  # Already 0-100

        return calculate_stress(
            load_avg=metrics["load_avg"],
            core_count=self._core_count,
            mem_available_pct=mem_available_pct,
            throttled=self._cached_throttled,
            latency_ratio=latency_ratio,
            io_rate=0,  # TODO: IOKit integration
            io_baseline=0,
            gpu_pct=self._cached_gpu_pct,
            wakeups_per_sec=self._cached_wakeups,
        )

    async def _handle_tier_action(self, action: str) -> None:
        """Handle tier state changes."""
        log.info("tier_action", action=action, tier=self.tier_manager.current_tier)

        if action in ("tier2_entry", "tier2_peak", "tier3_entry", "tier3_periodic"):
            self.buffer.snapshot_processes(trigger=action)

        if action == "tier2_exit":
            self.buffer.clear_snapshots()

        if self.on_tier_change:
            await self.on_tier_change(action, self.tier_manager.current_tier)

    async def _handle_potential_pause(self, actual: float, expected: float) -> None:
        """Handle potential pause detection."""
        log.warning("potential_pause", actual=actual, expected=expected, ratio=actual/expected)

        if self.on_pause_detected:
            await self.on_pause_detected(actual, expected, self.buffer.freeze())
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_sentinel.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/sentinel.py tests/test_sentinel.py
git commit -m "$(cat <<'EOF'
feat(sentinel): implement Sentinel class with fast/slow loops

Fast loop (100ms): collects load/memory, calculates stress,
pushes to ring buffer, manages tier transitions.

Slow loop (1s): placeholder for GPU/wakeups via powermetrics.

Pause detection via latency ratio > 2.0.
EOF
)"
```

---

## Phase 4: Database Migration

### Task 10: Add Status Column to Events Table

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write failing test for event status**

```python
def test_insert_event_with_status(db_connection):
    """Events can be inserted with status."""
    from pause_monitor.storage import insert_event, get_event_by_id, Event
    from pause_monitor.stress import StressBreakdown

    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    event = Event(
        timestamp=datetime.now(),
        duration=5.0,
        stress=stress,
        culprits=[],
        event_dir="/tmp/test",
        status="unreviewed",
    )

    event_id = insert_event(db_connection, event)
    retrieved = get_event_by_id(db_connection, event_id)

    assert retrieved.status == "unreviewed"


def test_update_event_status(db_connection):
    """Event status can be updated."""
    from pause_monitor.storage import insert_event, update_event_status, get_event_by_id, Event

    # Insert event
    event_id = insert_event(db_connection, event)

    # Update status
    update_event_status(db_connection, event_id, "reviewed")

    retrieved = get_event_by_id(db_connection, event_id)
    assert retrieved.status == "reviewed"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py::test_insert_event_with_status -v`
Expected: FAIL (Event doesn't have status field)

**Step 3: Update Event dataclass and schema**

```python
# In storage.py, update Event dataclass:
@dataclass
class Event:
    timestamp: datetime
    duration: float
    stress: StressBreakdown
    culprits: list
    event_dir: str
    status: str = "unreviewed"  # unreviewed, reviewed, pinned, dismissed
    notes: str | None = None
    id: int | None = None

# Update SCHEMA to include status column:
# In events table:
#     status          TEXT DEFAULT 'unreviewed',

# Add migration function:
def migrate_add_event_status(conn: sqlite3.Connection) -> None:
    """Add status column to events table if missing."""
    cursor = conn.execute("PRAGMA table_info(events)")
    columns = {row[1] for row in cursor.fetchall()}

    if "status" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN status TEXT DEFAULT 'reviewed'")
        conn.commit()
        log.info("migration_applied", migration="add_event_status")


def update_event_status(conn: sqlite3.Connection, event_id: int, status: str, notes: str | None = None) -> None:
    """Update event status and optionally notes."""
    if notes is not None:
        conn.execute(
            "UPDATE events SET status = ?, notes = ? WHERE id = ?",
            (status, notes, event_id),
        )
    else:
        conn.execute(
            "UPDATE events SET status = ? WHERE id = ?",
            (status, event_id),
        )
    conn.commit()
```

**Step 4: Update insert_event and get_event_by_id**

Add status handling to existing functions.

**Step 5: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat(storage): add status column to events table

Events now have lifecycle status:
- unreviewed (default for new)
- reviewed
- pinned (protected from pruning)
- dismissed (eligible for pruning)

Includes migration for existing databases.
EOF
)"
```

---

### Task 11: Update Pruning to Respect Event Status

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write failing test for status-aware pruning**

```python
def test_prune_respects_unreviewed_status(db_connection):
    """Unreviewed events are never pruned."""
    # Insert old unreviewed event
    old_time = datetime.now() - timedelta(days=60)
    event = Event(timestamp=old_time, ..., status="unreviewed")
    insert_event(db_connection, event)

    # Prune with 30 day retention
    pruned = prune_events(db_connection, retention_days=30)

    assert pruned == 0  # Unreviewed not pruned


def test_prune_respects_pinned_status(db_connection):
    """Pinned events are never pruned."""
    old_time = datetime.now() - timedelta(days=60)
    event = Event(timestamp=old_time, ..., status="pinned")
    insert_event(db_connection, event)

    pruned = prune_events(db_connection, retention_days=30)

    assert pruned == 0


def test_prune_removes_dismissed_events(db_connection):
    """Dismissed events are pruned after retention period."""
    old_time = datetime.now() - timedelta(days=60)
    event = Event(timestamp=old_time, ..., status="dismissed")
    event_id = insert_event(db_connection, event)

    pruned = prune_events(db_connection, retention_days=30)

    assert pruned == 1
    assert get_event_by_id(db_connection, event_id) is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py::test_prune_respects_unreviewed_status -v`
Expected: FAIL

**Step 3: Implement prune_events function**

```python
def prune_events(
    conn: sqlite3.Connection,
    retention_days: int,
    events_dir: Path | None = None,
) -> int:
    """Delete old events, respecting lifecycle status.

    Only prunes events with status 'reviewed' or 'dismissed'.
    Never prunes 'unreviewed' or 'pinned' events.

    Returns number of events pruned.
    """
    cutoff = time.time() - (retention_days * 86400)

    # Find prunable events
    cursor = conn.execute(
        """
        SELECT id, event_dir FROM events
        WHERE timestamp < ? AND status IN ('reviewed', 'dismissed')
        """,
        (cutoff,),
    )
    prunable = cursor.fetchall()

    for event_id, event_dir in prunable:
        # Delete event directory if it exists
        if event_dir and events_dir:
            event_path = Path(event_dir)
            if event_path.exists():
                import shutil
                shutil.rmtree(event_path)

        # Delete database record
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

    conn.commit()
    log.info("events_pruned", count=len(prunable), retention_days=retention_days)
    return len(prunable)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat(storage): implement status-aware event pruning

prune_events() only removes events with status 'reviewed' or
'dismissed' that are older than retention period. 'unreviewed'
and 'pinned' events are protected from automatic deletion.
EOF
)"
```

---

## Phase 5: Forensics Integration

### Task 12: Update Forensics to Accept Ring Buffer

**Files:**
- Modify: `src/pause_monitor/forensics.py`
- Test: `tests/test_forensics.py`

**Step 1: Write failing test**

```python
def test_forensics_capture_includes_ring_buffer(tmp_path):
    """ForensicsCapture writes ring buffer contents to event dir."""
    from pause_monitor.forensics import ForensicsCapture
    from pause_monitor.ringbuffer import RingBuffer, BufferContents
    from pause_monitor.stress import StressBreakdown

    # Create buffer with samples
    buffer = RingBuffer(max_samples=10)
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0, gpu=0, wakeups=0)
    buffer.push(stress, tier=1)
    buffer.push(stress, tier=2)
    frozen = buffer.freeze()

    # Create capture with buffer
    capture = ForensicsCapture(event_dir=tmp_path)
    capture.write_ring_buffer(frozen)

    # Verify file exists
    assert (tmp_path / "ring_buffer.json").exists()

    # Verify contents
    import json
    data = json.loads((tmp_path / "ring_buffer.json").read_text())
    assert len(data["samples"]) == 2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_forensics.py::test_forensics_capture_includes_ring_buffer -v`
Expected: FAIL (write_ring_buffer doesn't exist)

**Step 3: Implement write_ring_buffer**

```python
# In forensics.py, add to ForensicsCapture class:

def write_ring_buffer(self, contents: "BufferContents") -> None:
    """Write ring buffer contents to event directory."""
    import json
    from dataclasses import asdict

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
```

**Step 4: Run test**

Run: `uv run pytest tests/test_forensics.py::test_forensics_capture_includes_ring_buffer -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/forensics.py tests/test_forensics.py
git commit -m "$(cat <<'EOF'
feat(forensics): add ring buffer serialization

ForensicsCapture.write_ring_buffer() writes stress samples and
process snapshots to ring_buffer.json in event directory.
EOF
)"
```

---

### Task 13: Implement Culprit Identification

**Files:**
- Modify: `src/pause_monitor/forensics.py`
- Test: `tests/test_forensics.py`

**Step 1: Write failing tests**

```python
def test_identify_culprits_from_buffer():
    """identify_culprits correlates high stress factors with processes."""
    from pause_monitor.forensics import identify_culprits
    from pause_monitor.ringbuffer import BufferContents, RingSample, ProcessSnapshot, ProcessInfo
    from pause_monitor.stress import StressBreakdown

    # High memory stress
    samples = [
        RingSample(
            timestamp=datetime.now(),
            stress=StressBreakdown(load=5, memory=25, thermal=0, latency=0, io=0, gpu=0, wakeups=0),
            tier=2,
        )
    ]

    # Process snapshot with memory hog
    snapshots = [
        ProcessSnapshot(
            timestamp=datetime.now(),
            trigger="tier2_entry",
            by_cpu=[],
            by_memory=[ProcessInfo(pid=1, name="Chrome", cpu_pct=10, memory_mb=2048)],
        )
    ]

    contents = BufferContents(samples=samples, snapshots=snapshots)
    culprits = identify_culprits(contents)

    assert len(culprits) == 1
    assert culprits[0]["factor"] == "memory"
    assert "Chrome" in culprits[0]["processes"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_forensics.py::test_identify_culprits_from_buffer -v`
Expected: FAIL (identify_culprits doesn't exist)

**Step 3: Implement identify_culprits**

```python
def identify_culprits(contents: "BufferContents") -> list[dict]:
    """Identify likely culprits from ring buffer contents.

    Correlates high stress factors with processes from snapshots:
    - High memory stress → top memory consumers
    - High load stress → top CPU consumers
    - High GPU stress → GPU-intensive processes

    Returns list of {"factor": str, "score": int, "processes": [str]}
    """
    if not contents.samples:
        return []

    # Average stress factors over last 30 seconds
    avg_load = sum(s.stress.load for s in contents.samples) / len(contents.samples)
    avg_memory = sum(s.stress.memory for s in contents.samples) / len(contents.samples)
    avg_gpu = sum(s.stress.gpu for s in contents.samples) / len(contents.samples)
    avg_io = sum(s.stress.io for s in contents.samples) / len(contents.samples)
    avg_wakeups = sum(s.stress.wakeups for s in contents.samples) / len(contents.samples)

    # Collect processes from all snapshots
    all_by_cpu = []
    all_by_memory = []
    for snapshot in contents.snapshots:
        all_by_cpu.extend(snapshot.by_cpu)
        all_by_memory.extend(snapshot.by_memory)

    # Dedupe and sort
    cpu_names = list(dict.fromkeys(p.name for p in sorted(all_by_cpu, key=lambda p: p.cpu_pct, reverse=True)))[:5]
    mem_names = list(dict.fromkeys(p.name for p in sorted(all_by_memory, key=lambda p: p.memory_mb, reverse=True)))[:5]

    culprits = []

    # Threshold for considering a factor "elevated"
    if avg_memory >= 10:
        culprits.append({
            "factor": "memory",
            "score": int(avg_memory),
            "processes": mem_names,
        })

    if avg_load >= 10:
        culprits.append({
            "factor": "load",
            "score": int(avg_load),
            "processes": cpu_names,
        })

    if avg_gpu >= 10:
        culprits.append({
            "factor": "gpu",
            "score": int(avg_gpu),
            "processes": cpu_names,  # GPU processes typically high CPU too
        })

    if avg_io >= 10:
        culprits.append({
            "factor": "io",
            "score": int(avg_io),
            "processes": [],  # TODO: I/O tracking per process
        })

    if avg_wakeups >= 10:
        culprits.append({
            "factor": "wakeups",
            "score": int(avg_wakeups),
            "processes": [],  # TODO: Wakeups tracking per process
        })

    return sorted(culprits, key=lambda c: c["score"], reverse=True)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_forensics.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/forensics.py tests/test_forensics.py
git commit -m "$(cat <<'EOF'
feat(forensics): implement culprit identification

identify_culprits() analyzes ring buffer to correlate elevated
stress factors with processes from snapshots. Returns ranked
list of culprits with contributing processes.
EOF
)"
```

---

## Phase 6: CLI Updates

### Task 14: Add Event Status Management Commands

**Files:**
- Modify: `src/pause_monitor/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

```python
from click.testing import CliRunner


def test_events_list_shows_status(db_with_events):
    """Events list shows status column."""
    runner = CliRunner()
    result = runner.invoke(cli, ["events"])

    assert result.exit_code == 0
    assert "unreviewed" in result.output or "reviewed" in result.output


def test_events_filter_by_status(db_with_events):
    """Events can be filtered by status."""
    runner = CliRunner()
    result = runner.invoke(cli, ["events", "--status", "unreviewed"])

    assert result.exit_code == 0


def test_events_mark_reviewed(db_with_events):
    """Mark command changes event status."""
    runner = CliRunner()

    # Get first event ID
    list_result = runner.invoke(cli, ["events"])
    event_id = "1"  # Assuming ID 1 exists

    # Mark as reviewed
    result = runner.invoke(cli, ["events", "mark", event_id, "--reviewed"])

    assert result.exit_code == 0
    assert "reviewed" in result.output.lower()


def test_events_mark_with_notes(db_with_events):
    """Mark command can add notes."""
    runner = CliRunner()
    result = runner.invoke(cli, ["events", "mark", "1", "--reviewed", "--notes", "Chrome memory leak"])

    assert result.exit_code == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_events_mark_reviewed -v`
Expected: FAIL

**Step 3: Implement mark subcommand**

```python
# In cli.py, add mark subcommand to events group:

@events.command("mark")
@click.argument("event_id")
@click.option("--reviewed", is_flag=True, help="Mark as reviewed")
@click.option("--pinned", is_flag=True, help="Pin event (protected from pruning)")
@click.option("--dismissed", is_flag=True, help="Dismiss event (eligible for pruning)")
@click.option("--notes", help="Add notes to event")
@click.pass_context
def events_mark(ctx, event_id: str, reviewed: bool, pinned: bool, dismissed: bool, notes: str | None):
    """Change event status."""
    config = ctx.obj["config"]

    # Determine status
    if sum([reviewed, pinned, dismissed]) > 1:
        click.echo("Error: Only one status flag allowed", err=True)
        raise SystemExit(1)

    status = None
    if reviewed:
        status = "reviewed"
    elif pinned:
        status = "pinned"
    elif dismissed:
        status = "dismissed"

    if not status and not notes:
        click.echo("Error: Specify --reviewed, --pinned, --dismissed, or --notes", err=True)
        raise SystemExit(1)

    conn = get_connection(config.db_path)
    try:
        event = get_event_by_id(conn, int(event_id))
        if not event:
            click.echo(f"Error: Event {event_id} not found", err=True)
            raise SystemExit(1)

        if status:
            update_event_status(conn, int(event_id), status, notes)
            click.echo(f"Event {event_id} marked as {status}")
        elif notes:
            update_event_status(conn, int(event_id), event.status, notes)
            click.echo(f"Notes added to event {event_id}")
    finally:
        conn.close()
```

**Step 4: Update events list to show status**

```python
# In events command, update output format:
for event in events_list:
    status_icon = {
        "unreviewed": "●",
        "reviewed": "○",
        "pinned": "◆",
        "dismissed": "◇",
    }.get(event.status, "?")

    click.echo(f"{status_icon} [{event.id}] {event.timestamp:%Y-%m-%d %H:%M} "
               f"{event.duration:.1f}s pause  peak:{event.stress.total}  [{event.status}]")
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add event status management commands

- events list now shows status with icons (●○◆◇)
- events --status filters by status
- events mark <id> --reviewed/--pinned/--dismissed
- events mark <id> --notes "text" adds notes
EOF
)"
```

---

## Phase 7: Configuration Updates

### Task 15: Add Sentinel Configuration Options

**Files:**
- Modify: `src/pause_monitor/config.py`
- Test: `tests/test_config.py`

**Step 1: Write failing tests**

```python
def test_sentinel_config_defaults():
    """SentinelConfig has correct defaults."""
    from pause_monitor.config import SentinelConfig

    config = SentinelConfig()
    assert config.fast_interval_ms == 100
    assert config.slow_interval_ms == 1000
    assert config.ring_buffer_seconds == 30


def test_tiers_config_defaults():
    """TiersConfig has correct defaults."""
    from pause_monitor.config import TiersConfig

    config = TiersConfig()
    assert config.elevated_threshold == 15
    assert config.critical_threshold == 50


def test_config_loads_sentinel_section(tmp_path):
    """Config loads [sentinel] section from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sentinel]
fast_interval_ms = 200
slow_interval_ms = 2000
ring_buffer_seconds = 60

[tiers]
elevated_threshold = 20
critical_threshold = 60
""")

    config = Config.load(tmp_path / "config.toml")
    assert config.sentinel.fast_interval_ms == 200
    assert config.tiers.elevated_threshold == 20
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_sentinel_config_defaults -v`
Expected: FAIL (SentinelConfig doesn't exist)

**Step 3: Add new config dataclasses**

```python
# In config.py:

@dataclass
class SentinelConfig:
    """Sentinel timing configuration."""
    fast_interval_ms: int = 100
    slow_interval_ms: int = 1000
    ring_buffer_seconds: int = 30


@dataclass
class TiersConfig:
    """Tier threshold configuration."""
    elevated_threshold: int = 15
    critical_threshold: int = 50


# Update Config class to include new sections:
@dataclass
class Config:
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
    sentinel: SentinelConfig = field(default_factory=SentinelConfig)  # NEW
    tiers: TiersConfig = field(default_factory=TiersConfig)  # NEW
    learning_mode: bool = False
```

**Step 4: Update load/save methods**

Update `Config.load()` to parse `[sentinel]` and `[tiers]` sections.
Update `Config.save()` to write them.

**Step 5: Run tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat(config): add sentinel and tier threshold configuration

[sentinel]
fast_interval_ms = 100  # Fast loop interval
slow_interval_ms = 1000 # Slow loop interval
ring_buffer_seconds = 30

[tiers]
elevated_threshold = 15  # Stress threshold for tier 2
critical_threshold = 50  # Stress threshold for tier 3
EOF
)"
```

---

## Phase 8: Daemon Integration

### Task 16: Integrate Sentinel into Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write integration test**

```python
@pytest.mark.asyncio
async def test_daemon_uses_sentinel(mock_config):
    """Daemon starts sentinel instead of old adaptive sampling."""
    daemon = Daemon(mock_config)

    # Start daemon in background
    task = asyncio.create_task(daemon.start())
    await asyncio.sleep(0.5)  # Let it run
    daemon.stop()
    await task

    # Verify sentinel was used
    assert daemon.sentinel is not None
    assert len(daemon.ring_buffer.samples) > 0
```

**Step 2: Update Daemon to use Sentinel**

```python
# In daemon.py:

from pause_monitor.sentinel import Sentinel
from pause_monitor.ringbuffer import RingBuffer

class Daemon:
    def __init__(self, config: Config, db_path: Path | None = None):
        self.config = config
        self.db_path = db_path or config.db_path

        # Initialize ring buffer and sentinel
        max_samples = config.sentinel.ring_buffer_seconds * 10  # 10Hz
        self.ring_buffer = RingBuffer(max_samples=max_samples)
        self.sentinel = Sentinel(
            buffer=self.ring_buffer,
            fast_interval_ms=config.sentinel.fast_interval_ms,
            slow_interval_ms=config.sentinel.slow_interval_ms,
            elevated_threshold=config.tiers.elevated_threshold,
            critical_threshold=config.tiers.critical_threshold,
        )

        # Wire up callbacks
        self.sentinel.on_tier_change = self._handle_tier_change
        self.sentinel.on_pause_detected = self._handle_pause

        # ... rest of init

    async def _run_loop(self) -> None:
        """Main daemon loop - now delegates to sentinel."""
        await self.sentinel.start()

    async def _handle_tier_change(self, action: str, tier: int) -> None:
        """Handle tier state changes from sentinel."""
        if action == "tier2_entry":
            self.state.enter_elevated()
            log.info("elevated_entered", stress=self.sentinel.tier_manager.peak_stress)
        elif action == "tier2_exit":
            duration = self.state.exit_elevated()
            log.info("elevated_exited", duration=duration, peak=self.sentinel.tier_manager.peak_stress)
        elif action == "tier3_entry":
            self.state.enter_critical()
            await self._run_forensics("critical_entry")
        # etc.

    async def _handle_pause(self, actual: float, expected: float, buffer_contents: BufferContents) -> None:
        """Handle pause detected by sentinel."""
        # Check for sleep/wake
        if was_recently_asleep(window_seconds=int(actual) + 5):
            log.info("pause_was_sleep", duration=actual)
            return

        # Create event with ring buffer
        event_dir = create_event_dir(self.config.events_dir)
        capture = ForensicsCapture(event_dir)
        capture.write_ring_buffer(buffer_contents)

        # Identify culprits
        culprits = identify_culprits(buffer_contents)

        # Run additional forensics
        await run_full_capture(event_dir, duration=int(actual))

        # Store event
        event = Event(
            timestamp=datetime.now(),
            duration=actual,
            stress=buffer_contents.samples[-1].stress if buffer_contents.samples else StressBreakdown(...),
            culprits=culprits,
            event_dir=str(event_dir),
            status="unreviewed",
        )
        insert_event(self.conn, event)

        # Notify
        await self.notifier.pause_detected(actual, culprits)
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "$(cat <<'EOF'
feat(daemon): integrate sentinel for continuous monitoring

Daemon now uses Sentinel for stress sampling instead of
adaptive intervals. Ring buffer provides 30s pre-incident
history. Pause detection includes culprit identification
and ring buffer in forensics capture.
EOF
)"
```

---

## Phase 9: TUI Updates

### Task 17: Add Events Panel to TUI

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Test: `tests/test_tui.py` (if exists)

**Step 1: Implement EventsPanel widget**

```python
# In tui/app.py:

from textual.widgets import Static, DataTable
from textual.containers import Container


class EventsPanel(Container):
    """Panel showing recent pause events with status."""

    def compose(self) -> ComposeResult:
        yield Static("EVENTS", classes="panel-title")
        yield DataTable(id="events-table")

    def on_mount(self) -> None:
        table = self.query_one("#events-table", DataTable)
        table.add_columns("Status", "Time", "Duration", "Peak", "Culprits")

    def update_events(self, events: list[Event]) -> None:
        """Update table with events list."""
        table = self.query_one("#events-table", DataTable)
        table.clear()

        status_icons = {
            "unreviewed": "●",
            "reviewed": "○",
            "pinned": "◆",
            "dismissed": "◇",
        }

        for event in events[:10]:  # Show last 10
            icon = status_icons.get(event.status, "?")
            time_str = event.timestamp.strftime("%m-%d %H:%M")
            duration = f"{event.duration:.1f}s"
            peak = str(event.stress.total)

            culprits_str = ", ".join(
                f"{c['factor']}" for c in (event.culprits or [])[:2]
            ) or "-"

            table.add_row(icon, time_str, duration, peak, culprits_str)
```

**Step 2: Add keyboard shortcuts for event management**

```python
# In PauseMonitorApp:

BINDINGS = [
    ("r", "mark_reviewed", "Review"),
    ("p", "mark_pinned", "Pin"),
    ("d", "mark_dismissed", "Dismiss"),
    ("enter", "view_event", "Details"),
]

def action_mark_reviewed(self) -> None:
    """Mark selected event as reviewed."""
    # Get selected row from events table
    # Update status via storage
    pass

def action_view_event(self) -> None:
    """Show detailed event view."""
    # Push EventDetailScreen
    pass
```

**Step 3: Run and verify manually**

Run: `uv run pause-monitor tui`
Verify: Events panel shows with status icons and keyboard navigation works

**Step 4: Commit**

```bash
git add src/pause_monitor/tui/app.py
git commit -m "$(cat <<'EOF'
feat(tui): add events panel with status management

EventsPanel shows last 10 events with status icons.
Keyboard shortcuts: r=review, p=pin, d=dismiss, enter=details
EOF
)"
```

---

## Phase 10: Database Schema Migration

### Task 18: Add GPU and Wakeups Columns to Samples Table

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write failing test**

```python
def test_insert_sample_with_gpu_and_wakeups(db_connection):
    """Samples can include GPU and wakeups stress."""
    stress = StressBreakdown(
        load=10, memory=5, thermal=0, latency=0, io=0, gpu=15, wakeups=10
    )
    sample = Sample(
        timestamp=datetime.now(),
        interval=1.0,
        cpu_pct=50.0,
        load_avg=2.0,
        # ... other fields
        stress=stress,
    )

    sample_id = insert_sample(db_connection, sample)

    # Verify columns exist and values stored
    cursor = db_connection.execute(
        "SELECT stress_gpu, stress_wakeups FROM samples WHERE id = ?",
        (sample_id,)
    )
    row = cursor.fetchone()
    assert row[0] == 15
    assert row[1] == 10
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_insert_sample_with_gpu_and_wakeups -v`
Expected: FAIL (columns don't exist)

**Step 3: Update schema and migration**

```python
# Update SCHEMA constant to include new columns:
#     stress_gpu      INTEGER,
#     stress_wakeups  INTEGER,

# Add migration function:
def migrate_add_stress_columns(conn: sqlite3.Connection) -> None:
    """Add gpu and wakeups stress columns if missing."""
    cursor = conn.execute("PRAGMA table_info(samples)")
    columns = {row[1] for row in cursor.fetchall()}

    if "stress_gpu" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN stress_gpu INTEGER DEFAULT 0")
    if "stress_wakeups" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN stress_wakeups INTEGER DEFAULT 0")
    conn.commit()

# Update insert_sample to include new columns
# Update get_recent_samples to read new columns
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat(storage): add stress_gpu and stress_wakeups columns

Schema migration adds columns to existing databases.
insert_sample and get_recent_samples updated to handle
7-factor stress model.
EOF
)"
```

---

## Final Integration

### Task 19: End-to-End Integration Test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

```python
"""End-to-end integration tests for ring buffer sentinel."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch

from pause_monitor.daemon import Daemon
from pause_monitor.config import Config
from pause_monitor.storage import get_connection, get_events


@pytest.fixture
def temp_config(tmp_path):
    """Create temporary config with test paths."""
    config = Config()
    config._config_dir = tmp_path / "config"
    config._data_dir = tmp_path / "data"
    config._config_dir.mkdir(parents=True)
    config._data_dir.mkdir(parents=True)
    return config


@pytest.mark.asyncio
async def test_full_pause_detection_flow(temp_config):
    """Test complete flow: sentinel → pause → forensics → event."""
    daemon = Daemon(temp_config)

    # Start daemon
    task = asyncio.create_task(daemon.start())

    # Simulate stress buildup
    # (In real test, would mock metrics or wait for actual system load)
    await asyncio.sleep(1.0)

    # Verify ring buffer has samples
    assert len(daemon.ring_buffer.samples) > 0

    # Stop daemon
    daemon.stop()
    await task

    # Verify no crashes, clean shutdown


@pytest.mark.asyncio
async def test_tier_escalation_creates_snapshots(temp_config):
    """Verify tier 2 entry triggers process snapshot."""
    daemon = Daemon(temp_config)

    # Mock high stress
    with patch.object(daemon.sentinel, '_calculate_fast_stress') as mock_stress:
        mock_stress.return_value = StressBreakdown(
            load=20, memory=10, thermal=0, latency=0, io=0, gpu=0, wakeups=0
        )

        task = asyncio.create_task(daemon.start())
        await asyncio.sleep(0.3)
        daemon.stop()
        await task

    # Should have triggered tier 2 and snapshot
    assert daemon.sentinel.tier_manager.current_tier >= 2
    assert len(daemon.ring_buffer.snapshots) > 0
```

**Step 2: Run integration tests**

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "$(cat <<'EOF'
test: add end-to-end integration tests

Tests full flow from sentinel sampling through pause detection
to event creation. Verifies tier escalation and process snapshots.
EOF
)"
```

---

### Task 20: Update Documentation

**Files:**
- Modify: `CLAUDE.md` (Architecture section)
- Modify: `.serena/memories/implementation_guide.md`

**Step 1: Update CLAUDE.md architecture**

Add sentinel to module table, update key design decisions.

**Step 2: Update implementation_guide memory**

Add sentinel.py and ringbuffer.py module documentation.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: update architecture for sentinel implementation

Documents new sentinel.py and ringbuffer.py modules.
Updates design decisions to reflect tiered monitoring.
EOF
)"
```

---

## Summary

**Total Tasks:** 20
**Estimated New Code:** ~1500 lines
**New Modules:** `sentinel.py`, `ringbuffer.py`
**Modified Modules:** `stress.py`, `daemon.py`, `storage.py`, `forensics.py`, `config.py`, `cli.py`, `tui/app.py`

**Key Dependencies:**
- Phase 1 (Tasks 1-3): Stress model expansion — required for all subsequent phases
- Phase 2 (Tasks 4-6): Ring buffer — required for sentinel
- Phase 3 (Tasks 7-9): Sentinel — core feature, depends on phases 1-2
- Phase 4-5 (Tasks 10-13): Storage + Forensics — can run in parallel
- Phase 6-7 (Tasks 14-15): CLI + Config — can run in parallel
- Phase 8 (Task 16): Daemon integration — depends on phases 1-5
- Phase 9-10 (Tasks 17-18): TUI + Schema — can run after phase 4
- Final (Tasks 19-20): Integration + Docs — last
