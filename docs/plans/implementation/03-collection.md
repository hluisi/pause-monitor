# Part 3: Collection

> **Navigation:** [Index](./index.md) | [Prev: Storage](./02-storage.md) | **Current** | [Next: Response](./04-response.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 5-6 (Metrics Collection + Sleep/Wake and Pause Detection)
**Tasks:** 12-17
**Dependencies:** Part 1 (stress.py), Part 2 (storage.py)

---

## Phase 5: Metrics Collection

### Task 12: Powermetrics Plist Parser

**Files:**
- Create: `src/pause_monitor/collector.py`
- Create: `tests/test_collector.py`

**Step 1: Write failing tests for plist parsing**

Create `tests/test_collector.py`:

```python
"""Tests for metrics collector."""

import plistlib
from datetime import datetime

import pytest

from pause_monitor.collector import parse_powermetrics_sample


SAMPLE_PLIST = {
    "timestamp": datetime.now(),
    "processor": {
        "cpu_power": 5.5,
        "combined_power": 10.0,
        "package_idle_residency": 45.0,
        "clusters": [
            {
                "name": "E-Cluster",
                "cpus": [
                    {"cpu": 0, "freq_hz": 972_000_000, "idle_percent": 90.0},
                    {"cpu": 1, "freq_hz": 972_000_000, "idle_percent": 85.0},
                ],
            },
            {
                "name": "P-Cluster",
                "cpus": [
                    {"cpu": 4, "freq_hz": 3_500_000_000, "idle_percent": 50.0},
                    {"cpu": 5, "freq_hz": 3_500_000_000, "idle_percent": 55.0},
                ],
            },
        ],
    },
    "gpu": {"freq_hz": 1_398_000_000, "busy_percent": 25.0, "gpu_power": 2.5},
    "thermal_pressure": "Nominal",
    "is_charging": True,
}


def test_parse_powermetrics_cpu_usage():
    """Parser extracts CPU usage from idle percentages."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    # Average of (100-90 + 100-85 + 100-50 + 100-55) / 4 = 30%
    assert 29.0 <= result.cpu_pct <= 31.0


def test_parse_powermetrics_cpu_freq():
    """Parser extracts max CPU frequency."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.cpu_freq == 3500  # MHz


def test_parse_powermetrics_gpu():
    """Parser extracts GPU busy percentage."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.gpu_pct == 25.0


def test_parse_powermetrics_throttled_nominal():
    """Nominal thermal pressure means not throttled."""
    data = plistlib.dumps(SAMPLE_PLIST)
    result = parse_powermetrics_sample(data)

    assert result.throttled is False


def test_parse_powermetrics_throttled_critical():
    """Critical thermal pressure means throttled."""
    plist = SAMPLE_PLIST.copy()
    plist["thermal_pressure"] = "Heavy"
    data = plistlib.dumps(plist)
    result = parse_powermetrics_sample(data)

    assert result.throttled is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_parse_powermetrics_cpu_usage -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement plist parser**

Create `src/pause_monitor/collector.py`:

```python
"""Metrics collector using powermetrics."""

import plistlib
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass
class PowermetricsResult:
    """Parsed powermetrics sample data."""

    cpu_pct: float | None
    cpu_freq: int | None  # MHz
    cpu_temp: float | None
    throttled: bool | None
    gpu_pct: float | None


def parse_powermetrics_sample(data: bytes) -> PowermetricsResult:
    """Parse a single powermetrics plist sample.

    Args:
        data: Raw plist bytes from powermetrics output

    Returns:
        PowermetricsResult with extracted metrics
    """
    try:
        plist = plistlib.loads(data)
    except plistlib.InvalidFileException:
        log.warning("invalid_plist_data")
        return PowermetricsResult(
            cpu_pct=None,
            cpu_freq=None,
            cpu_temp=None,
            throttled=None,
            gpu_pct=None,
        )

    # Extract CPU usage from cluster data
    cpu_pct = _extract_cpu_usage(plist.get("processor", {}))

    # Extract max CPU frequency
    cpu_freq = _extract_cpu_freq(plist.get("processor", {}))

    # CPU temperature (not always available)
    cpu_temp = None
    if "processor" in plist and "cpu_thermal_level" in plist["processor"]:
        cpu_temp = plist["processor"]["cpu_thermal_level"]

    # Thermal throttling
    thermal_pressure = plist.get("thermal_pressure", "Nominal")
    throttled = thermal_pressure in ("Moderate", "Heavy", "Critical", "Sleeping")

    # GPU usage
    gpu_data = plist.get("gpu", {})
    gpu_pct = gpu_data.get("busy_percent")

    return PowermetricsResult(
        cpu_pct=cpu_pct,
        cpu_freq=cpu_freq,
        cpu_temp=cpu_temp,
        throttled=throttled,
        gpu_pct=gpu_pct,
    )


def _extract_cpu_usage(processor: dict[str, Any]) -> float | None:
    """Extract CPU usage percentage from processor data."""
    clusters = processor.get("clusters", [])
    if not clusters:
        return None

    total_usage = 0.0
    cpu_count = 0

    for cluster in clusters:
        for cpu in cluster.get("cpus", []):
            idle_pct = cpu.get("idle_percent", 100.0)
            total_usage += 100.0 - idle_pct
            cpu_count += 1

    return total_usage / cpu_count if cpu_count > 0 else None


def _extract_cpu_freq(processor: dict[str, Any]) -> int | None:
    """Extract maximum CPU frequency in MHz."""
    clusters = processor.get("clusters", [])
    if not clusters:
        return None

    max_freq_hz = 0
    for cluster in clusters:
        for cpu in cluster.get("cpus", []):
            freq_hz = cpu.get("freq_hz", 0)
            max_freq_hz = max(max_freq_hz, freq_hz)

    return max_freq_hz // 1_000_000 if max_freq_hz > 0 else None
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add powermetrics plist parser"
```

---

### Task 13: Streaming Powermetrics Subprocess

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write failing tests for streaming subprocess**

Add to `tests/test_collector.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch

from pause_monitor.collector import PowermetricsStream, StreamStatus


@pytest.mark.asyncio
async def test_powermetrics_stream_status_not_started():
    """Stream status is NOT_STARTED before starting."""
    stream = PowermetricsStream(interval_ms=1000)
    assert stream.status == StreamStatus.NOT_STARTED


@pytest.mark.asyncio
async def test_powermetrics_stream_status_running():
    """Stream status is RUNNING after start."""
    stream = PowermetricsStream(interval_ms=1000)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stdout.__aiter__ = AsyncMock(return_value=iter([]))
        mock_exec.return_value = mock_process

        await stream.start()
        assert stream.status == StreamStatus.RUNNING

        await stream.stop()


@pytest.mark.asyncio
async def test_powermetrics_stream_stop():
    """Stream can be stopped cleanly."""
    stream = PowermetricsStream(interval_ms=1000)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = AsyncMock()
        mock_process.wait = AsyncMock()
        mock_exec.return_value = mock_process

        await stream.start()
        await stream.stop()

        mock_process.terminate.assert_called_once()
        assert stream.status == StreamStatus.STOPPED
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_powermetrics_stream_status_not_started -v`
Expected: FAIL with ImportError

**Step 3: Implement streaming subprocess**

Add to `src/pause_monitor/collector.py`:

```python
import asyncio
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from enum import Enum


class StreamStatus(Enum):
    """Powermetrics stream status."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class PowermetricsStream:
    """Async stream of powermetrics data.

    Uses streaming plist output for lower latency than exec-per-sample.
    """

    POWERMETRICS_CMD = [
        "/usr/bin/powermetrics",
        "--samplers", "cpu_power,gpu_power,thermal",
        "--output-format", "plist",
    ]

    def __init__(self, interval_ms: int = 1000):
        self.interval_ms = interval_ms
        self._process: Process | None = None
        self._status = StreamStatus.NOT_STARTED
        self._buffer = b""

    @property
    def status(self) -> StreamStatus:
        """Current stream status."""
        return self._status

    async def start(self) -> None:
        """Start the powermetrics subprocess."""
        if self._process is not None:
            return

        cmd = self.POWERMETRICS_CMD + ["-i", str(self.interval_ms)]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._status = StreamStatus.RUNNING
            log.info("powermetrics_started", interval_ms=self.interval_ms)
        except (FileNotFoundError, PermissionError) as e:
            self._status = StreamStatus.FAILED
            log.error("powermetrics_start_failed", error=str(e))
            raise

    async def stop(self) -> None:
        """Stop the powermetrics subprocess."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            await self._process.wait()
        except ProcessLookupError:
            pass

        self._process = None
        self._status = StreamStatus.STOPPED
        log.info("powermetrics_stopped")

    async def read_samples(self) -> AsyncIterator[PowermetricsResult]:
        """Yield parsed samples as they become available.

        powermetrics outputs plists separated by NUL bytes (\\0).
        """
        if self._process is None or self._process.stdout is None:
            return

        async for chunk in self._process.stdout:
            self._buffer += chunk

            # powermetrics separates plists with NUL bytes
            while b"\0" in self._buffer:
                plist_data, self._buffer = self._buffer.split(b"\0", 1)

                # Skip empty chunks
                if not plist_data.strip():
                    continue

                result = parse_powermetrics_sample(plist_data)
                yield result
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add streaming powermetrics subprocess"
```

---

### Task 14: System Metrics Collection

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write failing tests for system metrics**

Add to `tests/test_collector.py`:

```python
from pause_monitor.collector import get_system_metrics, get_core_count


def test_get_core_count():
    """get_core_count returns positive integer."""
    count = get_core_count()
    assert count > 0


def test_get_system_metrics_returns_complete():
    """get_system_metrics returns all required fields."""
    metrics = get_system_metrics()

    assert metrics.load_avg is not None
    assert metrics.mem_available is not None
    assert metrics.swap_used is not None
    assert metrics.io_read is not None
    assert metrics.io_write is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_get_core_count -v`
Expected: FAIL with ImportError

**Step 3: Implement system metrics collection**

Add to `src/pause_monitor/collector.py`:

```python
import os
import subprocess


@dataclass
class SystemMetrics:
    """Non-powermetrics system metrics."""

    load_avg: float
    mem_available: int
    swap_used: int
    io_read: int
    io_write: int
    net_sent: int
    net_recv: int


def get_core_count() -> int:
    """Get number of CPU cores."""
    return os.cpu_count() or 1


def get_system_metrics() -> SystemMetrics:
    """Get system metrics not provided by powermetrics.

    Uses os module and sysctl for most metrics.
    """
    # Load average
    load_avg = os.getloadavg()[0]  # 1-minute average

    # Memory via sysctl (faster than subprocess)
    mem_available = _get_memory_available()

    # Swap via sysctl
    swap_used = _get_swap_used()

    # I/O counters via ioreg (macOS specific)
    io_read, io_write = _get_io_counters()

    # Network counters via netstat
    net_sent, net_recv = _get_network_counters()

    return SystemMetrics(
        load_avg=load_avg,
        mem_available=mem_available,
        swap_used=swap_used,
        io_read=io_read,
        io_write=io_write,
        net_sent=net_sent,
        net_recv=net_recv,
    )


def _get_memory_available() -> int:
    """Get available memory in bytes via sysctl."""
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libc.dylib")

    # Get page size
    page_size = ctypes.c_size_t(4)
    page_value = ctypes.c_int()
    libc.sysctlbyname(
        b"hw.pagesize",
        ctypes.byref(page_value),
        ctypes.byref(page_size),
        None,
        0,
    )
    page_size_bytes = page_value.value

    # Get free + inactive pages as "available"
    vm_size = ctypes.c_size_t(4)
    free_pages = ctypes.c_int()
    libc.sysctlbyname(
        b"vm.page_free_count",
        ctypes.byref(free_pages),
        ctypes.byref(vm_size),
        None,
        0,
    )

    return free_pages.value * page_size_bytes


def _get_swap_used() -> int:
    """Get swap usage in bytes."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        # Parse: "total = 1024.00M  used = 256.00M  free = 768.00M"
        for part in result.stdout.split():
            if part.endswith("M") and "used" in result.stdout.split()[result.stdout.split().index(part) - 2]:
                return int(float(part[:-1]) * 1024 * 1024)
        return 0
    except (subprocess.TimeoutExpired, IndexError, ValueError):
        return 0


def _get_io_counters() -> tuple[int, int]:
    """Get disk I/O bytes (read, write)."""
    # Placeholder - actual implementation would use IOKit
    return 0, 0


def _get_network_counters() -> tuple[int, int]:
    """Get network bytes (sent, received)."""
    # Placeholder - actual implementation would parse netstat
    return 0, 0
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add system metrics collection"
```

---

### Task 15: Sample Collection Policy

**Files:**
- Modify: `src/pause_monitor/collector.py`
- Modify: `tests/test_collector.py`

**Step 1: Write failing tests for sample policy**

Add to `tests/test_collector.py`:

```python
from pause_monitor.collector import SamplePolicy, SamplingState
from pause_monitor.stress import StressBreakdown


def test_sample_policy_initial_state():
    """SamplePolicy starts in NORMAL state."""
    policy = SamplePolicy(normal_interval=5, elevated_interval=1)
    assert policy.state == SamplingState.NORMAL
    assert policy.current_interval == 5


def test_sample_policy_elevates_on_threshold():
    """SamplePolicy elevates when stress exceeds threshold."""
    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
    )

    stress = StressBreakdown(load=20, memory=15, thermal=0, latency=0, io=0)
    policy.update(stress)

    assert policy.state == SamplingState.ELEVATED
    assert policy.current_interval == 1


def test_sample_policy_returns_to_normal():
    """SamplePolicy returns to normal when stress drops below de-elevation threshold."""
    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        de_elevation_threshold=20,
        cooldown_samples=3,
    )

    # Elevate
    high_stress = StressBreakdown(load=40, memory=0, thermal=0, latency=0, io=0)
    policy.update(high_stress)
    assert policy.state == SamplingState.ELEVATED

    # Drop below de-elevation threshold for cooldown period
    low_stress = StressBreakdown(load=5, memory=0, thermal=0, latency=0, io=0)
    for _ in range(3):
        policy.update(low_stress)

    assert policy.state == SamplingState.NORMAL


def test_sample_policy_hysteresis():
    """SamplePolicy stays elevated when stress is between thresholds (hysteresis)."""
    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        de_elevation_threshold=20,
        cooldown_samples=3,
    )

    # Elevate
    high_stress = StressBreakdown(load=40, memory=0, thermal=0, latency=0, io=0)
    policy.update(high_stress)
    assert policy.state == SamplingState.ELEVATED

    # Drop to 25 (between 20 and 30) - should stay elevated
    mid_stress = StressBreakdown(load=25, memory=0, thermal=0, latency=0, io=0)
    for _ in range(10):  # Even many samples shouldn't trigger de-elevation
        policy.update(mid_stress)

    assert policy.state == SamplingState.ELEVATED  # Still elevated due to hysteresis


def test_sample_policy_critical_triggers_snapshot():
    """SamplePolicy triggers snapshot on critical stress."""
    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        critical_threshold=60,
    )

    stress = StressBreakdown(load=40, memory=20, thermal=20, latency=0, io=0)
    result = policy.update(stress)

    assert result.should_snapshot is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_sample_policy_initial_state -v`
Expected: FAIL with ImportError

**Step 3: Implement sample policy**

Add to `src/pause_monitor/collector.py`:

```python
from enum import Enum


class SamplingState(Enum):
    """Current sampling rate state."""

    NORMAL = "normal"
    ELEVATED = "elevated"


@dataclass
class PolicyResult:
    """Result of policy update."""

    should_snapshot: bool = False
    state_changed: bool = False


class SamplePolicy:
    """Adaptive sampling policy based on stress levels.

    Uses hysteresis to prevent oscillation: elevate at 30, de-elevate at 20.
    """

    def __init__(
        self,
        normal_interval: int = 5,
        elevated_interval: int = 1,
        elevation_threshold: int = 30,
        de_elevation_threshold: int = 20,  # Hysteresis: lower threshold to return to normal
        critical_threshold: int = 60,
        cooldown_samples: int = 5,
    ):
        self.normal_interval = normal_interval
        self.elevated_interval = elevated_interval
        self.elevation_threshold = elevation_threshold
        self.de_elevation_threshold = de_elevation_threshold
        self.critical_threshold = critical_threshold
        self.cooldown_samples = cooldown_samples

        self._state = SamplingState.NORMAL
        self._samples_below_threshold = 0

    @property
    def state(self) -> SamplingState:
        """Current sampling state."""
        return self._state

    @property
    def current_interval(self) -> int:
        """Current sampling interval in seconds."""
        if self._state == SamplingState.ELEVATED:
            return self.elevated_interval
        return self.normal_interval

    def update(self, stress: StressBreakdown) -> PolicyResult:
        """Update policy based on current stress.

        Returns:
            PolicyResult indicating if snapshot should be taken
        """
        result = PolicyResult()
        total = stress.total

        # Check for critical stress
        if total >= self.critical_threshold:
            result.should_snapshot = True

        # State transitions with hysteresis
        old_state = self._state

        if total >= self.elevation_threshold:
            # Elevate when stress reaches upper threshold
            self._state = SamplingState.ELEVATED
            self._samples_below_threshold = 0
        elif self._state == SamplingState.ELEVATED and total < self.de_elevation_threshold:
            # Only de-elevate when below lower threshold (hysteresis)
            self._samples_below_threshold += 1
            if self._samples_below_threshold >= self.cooldown_samples:
                self._state = SamplingState.NORMAL
        elif self._state == SamplingState.ELEVATED:
            # Between thresholds - stay elevated but don't accumulate cooldown
            self._samples_below_threshold = 0

        result.state_changed = old_state != self._state

        if result.state_changed:
            log.info(
                "sampling_state_changed",
                old=old_state.value,
                new=self._state.value,
                stress=total,
            )

        return result
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (15 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/collector.py tests/test_collector.py
git commit -m "feat(collector): add adaptive sampling policy"
```

---

## Phase 6: Sleep/Wake and Pause Detection

### Task 16: Sleep/Wake Detection

**Files:**
- Create: `src/pause_monitor/sleepwake.py`
- Create: `tests/test_sleepwake.py`

**Step 1: Write failing tests for sleep/wake parser**

Create `tests/test_sleepwake.py`:

```python
"""Tests for sleep/wake detection."""

from datetime import datetime, timedelta

import pytest

from pause_monitor.sleepwake import (
    SleepWakeEvent,
    SleepWakeType,
    parse_pmset_log,
    get_recent_sleep_events,
)


SAMPLE_PMSET_OUTPUT = """
2024-01-15 10:30:15 -0500 Sleep                   Entering Sleep state due to 'Software Sleep pid=1234':
2024-01-15 10:30:20 -0500 Kernel Idle sleep preventers: IODisplayWrangler
2024-01-15 10:35:45 -0500 Wake                    Wake from Normal Sleep [CDNVA] : due to EC.LidOpen/Lid Open
2024-01-15 14:20:00 -0500 Sleep                   Entering Sleep state due to 'Idle Sleep':
2024-01-15 14:45:30 -0500 DarkWake                DarkWake from Normal Sleep [CDN] : due to EC.PowerButton/
"""


def test_parse_pmset_finds_sleep_events():
    """Parser extracts sleep events from pmset output."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    sleep_events = [e for e in events if e.event_type == SleepWakeType.SLEEP]
    assert len(sleep_events) == 2


def test_parse_pmset_finds_wake_events():
    """Parser extracts wake events from pmset output."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    wake_events = [e for e in events if e.event_type == SleepWakeType.WAKE]
    assert len(wake_events) == 1


def test_parse_pmset_finds_darkwake_events():
    """Parser extracts DarkWake events."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    darkwake_events = [e for e in events if e.event_type == SleepWakeType.DARK_WAKE]
    assert len(darkwake_events) == 1


def test_parse_pmset_extracts_timestamp():
    """Parser extracts correct timestamps."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    assert first_sleep.timestamp.year == 2024
    assert first_sleep.timestamp.month == 1
    assert first_sleep.timestamp.day == 15


def test_parse_pmset_extracts_reason():
    """Parser extracts sleep/wake reason."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    assert "Software Sleep" in first_sleep.reason


def test_sleep_wake_event_duration():
    """SleepWakeEvent calculates duration to next event."""
    events = parse_pmset_log(SAMPLE_PMSET_OUTPUT)

    first_sleep = events[0]
    first_wake = events[1]

    duration = (first_wake.timestamp - first_sleep.timestamp).total_seconds()
    assert 300 <= duration <= 400  # About 5 minutes of sleep
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sleepwake.py::test_parse_pmset_finds_sleep_events -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement sleep/wake detection**

Create `src/pause_monitor/sleepwake.py`:

```python
"""Sleep/Wake detection for pause-monitor."""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import structlog

log = structlog.get_logger()


class SleepWakeType(Enum):
    """Type of sleep/wake event."""

    SLEEP = "sleep"
    WAKE = "wake"
    DARK_WAKE = "dark_wake"


@dataclass
class SleepWakeEvent:
    """A single sleep or wake event."""

    timestamp: datetime
    event_type: SleepWakeType
    reason: str


# Pattern to match pmset log entries
PMSET_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+[+-]\d{4}\s+"
    r"(Sleep|Wake|DarkWake)\s+(.+)"
)


def parse_pmset_log(output: str) -> list[SleepWakeEvent]:
    """Parse pmset -g log output for sleep/wake events.

    Args:
        output: Raw output from `pmset -g log`

    Returns:
        List of SleepWakeEvent in chronological order
    """
    events = []

    for line in output.splitlines():
        match = PMSET_PATTERN.search(line)
        if not match:
            continue

        timestamp_str, event_type_str, reason = match.groups()

        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        if event_type_str == "Sleep":
            event_type = SleepWakeType.SLEEP
        elif event_type_str == "Wake":
            event_type = SleepWakeType.WAKE
        elif event_type_str == "DarkWake":
            event_type = SleepWakeType.DARK_WAKE
        else:
            continue

        events.append(SleepWakeEvent(
            timestamp=timestamp,
            event_type=event_type,
            reason=reason.strip(),
        ))

    return events


def get_recent_sleep_events(since: datetime | None = None) -> list[SleepWakeEvent]:
    """Get recent sleep/wake events from system logs.

    Args:
        since: Only return events after this time. Defaults to 1 hour ago.

    Returns:
        List of SleepWakeEvent in chronological order
    """
    if since is None:
        since = datetime.now() - timedelta(hours=1)

    try:
        result = subprocess.run(
            ["pmset", "-g", "log"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        events = parse_pmset_log(result.stdout)
        return [e for e in events if e.timestamp >= since]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("pmset_log_failed", error=str(e))
        return []


def was_recently_asleep(within_seconds: float = 10.0) -> SleepWakeEvent | None:
    """Check if system recently woke from sleep.

    Args:
        within_seconds: How recent counts as "recent"

    Returns:
        The wake event if found, None otherwise
    """
    now = datetime.now()
    events = get_recent_sleep_events(since=now - timedelta(seconds=within_seconds * 2))

    for event in reversed(events):
        if event.event_type in (SleepWakeType.WAKE, SleepWakeType.DARK_WAKE):
            if (now - event.timestamp).total_seconds() <= within_seconds:
                return event

    return None
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_sleepwake.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/sleepwake.py tests/test_sleepwake.py
git commit -m "feat(sleepwake): add sleep/wake event detection"
```

---

### Task 17: Pause Detection Logic

**Files:**
- Modify: `src/pause_monitor/sleepwake.py`
- Modify: `tests/test_sleepwake.py`

**Step 1: Write failing tests for pause detection**

Add to `tests/test_sleepwake.py`:

```python
from pause_monitor.sleepwake import PauseDetector, PauseEvent


def test_pause_detector_no_pause_normal_latency():
    """No pause detected when latency is normal."""
    detector = PauseDetector(expected_interval=5.0)

    result = detector.check(actual_interval=5.2)
    assert result is None


def test_pause_detector_detects_pause():
    """Pause detected when actual interval >> expected."""
    detector = PauseDetector(expected_interval=5.0, pause_threshold=2.0)

    result = detector.check(actual_interval=15.0)

    assert result is not None
    assert isinstance(result, PauseEvent)
    assert result.duration == 15.0
    assert result.expected == 5.0


def test_pause_detector_ignores_sleep():
    """Pause not flagged if system was recently asleep."""
    detector = PauseDetector(expected_interval=5.0)

    # Simulate wake event
    from pause_monitor.sleepwake import SleepWakeEvent, SleepWakeType

    wake_event = SleepWakeEvent(
        timestamp=datetime.now(),
        event_type=SleepWakeType.WAKE,
        reason="Lid Open",
    )

    result = detector.check(actual_interval=60.0, recent_wake=wake_event)

    assert result is None  # Not a pause, just woke up


def test_pause_detector_latency_ratio():
    """PauseEvent includes latency ratio."""
    detector = PauseDetector(expected_interval=5.0)

    result = detector.check(actual_interval=25.0)

    assert result is not None
    assert result.latency_ratio == 5.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sleepwake.py::test_pause_detector_no_pause_normal_latency -v`
Expected: FAIL with ImportError

**Step 3: Implement pause detection**

Add to `src/pause_monitor/sleepwake.py`:

```python
@dataclass
class PauseEvent:
    """A detected system pause (not sleep-related)."""

    timestamp: datetime
    duration: float
    expected: float
    latency_ratio: float


class PauseDetector:
    """Detect system pauses via timing anomalies."""

    def __init__(self, expected_interval: float, pause_threshold: float = 2.0):
        """Initialize pause detector.

        Args:
            expected_interval: Expected seconds between samples
            pause_threshold: Ratio above which is considered a pause
        """
        self.expected_interval = expected_interval
        self.pause_threshold = pause_threshold

    def check(
        self,
        actual_interval: float,
        recent_wake: SleepWakeEvent | None = None,
    ) -> PauseEvent | None:
        """Check if the interval indicates a pause.

        Args:
            actual_interval: Actual time elapsed since last sample
            recent_wake: If system recently woke from sleep

        Returns:
            PauseEvent if pause detected, None otherwise
        """
        latency_ratio = actual_interval / self.expected_interval

        # Not a pause if ratio is below threshold
        if latency_ratio < self.pause_threshold:
            return None

        # Not a pause if we just woke from sleep
        if recent_wake is not None:
            log.debug(
                "pause_suppressed_by_wake",
                actual=actual_interval,
                expected=self.expected_interval,
                wake_reason=recent_wake.reason,
            )
            return None

        return PauseEvent(
            timestamp=datetime.now(),
            duration=actual_interval,
            expected=self.expected_interval,
            latency_ratio=latency_ratio,
        )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_sleepwake.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/sleepwake.py tests/test_sleepwake.py
git commit -m "feat(sleepwake): add pause detection logic"
```

---


---

> **Next:** [Part 4: Response](./04-response.md)
