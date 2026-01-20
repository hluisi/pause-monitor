# Part 1: Foundation

> **Navigation:** [Index](./index.md) | **Current** | [Next: Storage](./02-storage.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 1-2 (Core Infrastructure + Stress Detection)
**Tasks:** 1-7
**Dependencies:** None (this is the foundation)

---

## Phase 1: Core Infrastructure

### Task 1: Remove Unnecessary Dependency

The design doc states pyobjc is NOT required. Remove it.

**Files:**
- Modify: `pyproject.toml`

**Step 1: Remove pyobjc dependency**

Edit `pyproject.toml` to remove the line containing `"pyobjc-framework-Cocoa>=10.0",` from the dependencies list.

**Step 2: Sync dependencies**

Run: `uv sync`
Expected: Success, lockfile updated without pyobjc

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: remove unused pyobjc dependency"
```

---

### Task 2: Configuration Dataclasses

**Files:**
- Create: `src/pause_monitor/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test for config dataclasses**

Create `tests/test_config.py`:

```python
"""Tests for configuration system."""

from pathlib import Path

import pytest

from pause_monitor.config import (
    AlertsConfig,
    Config,
    RetentionConfig,
    SamplingConfig,
    SuspectsConfig,
)


def test_sampling_config_defaults():
    """SamplingConfig has correct defaults."""
    config = SamplingConfig()
    assert config.normal_interval == 5
    assert config.elevated_interval == 1
    assert config.elevation_threshold == 30
    assert config.critical_threshold == 60


def test_retention_config_defaults():
    """RetentionConfig has correct defaults."""
    config = RetentionConfig()
    assert config.samples_days == 30
    assert config.events_days == 90


def test_alerts_config_defaults():
    """AlertsConfig has correct defaults."""
    config = AlertsConfig()
    assert config.enabled is True
    assert config.pause_detected is True
    assert config.pause_min_duration == 2.0
    assert config.critical_stress is True
    assert config.critical_threshold == 60
    assert config.critical_duration == 30
    assert config.elevated_entered is False
    assert config.forensics_completed is True
    assert config.sound is True


def test_suspects_config_defaults():
    """SuspectsConfig has correct default patterns."""
    config = SuspectsConfig()
    assert "codemeter" in config.patterns
    assert "biomesyncd" in config.patterns
    assert "kernel_task" in config.patterns


def test_full_config_defaults():
    """Full Config object has correct nested defaults."""
    config = Config()
    assert config.sampling.normal_interval == 5
    assert config.retention.samples_days == 30
    assert config.alerts.enabled is True
    assert config.learning_mode is False


def test_config_paths():
    """Config provides correct data paths."""
    config = Config()
    assert "pause-monitor" in str(config.config_dir)
    assert "pause-monitor" in str(config.data_dir)
    assert config.db_path.name == "data.db"
    assert config.pid_path.name == "daemon.pid"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Create config module**

Create `src/pause_monitor/config.py`:

```python
"""Configuration system for pause-monitor."""

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit


@dataclass
class SamplingConfig:
    """Sampling interval configuration."""

    normal_interval: int = 5
    elevated_interval: int = 1
    elevation_threshold: int = 30
    critical_threshold: int = 60


@dataclass
class RetentionConfig:
    """Data retention configuration."""

    samples_days: int = 30
    events_days: int = 90


@dataclass
class AlertsConfig:
    """Alert notification configuration."""

    enabled: bool = True
    pause_detected: bool = True
    pause_min_duration: float = 2.0
    critical_stress: bool = True
    critical_threshold: int = 60
    critical_duration: int = 30
    elevated_entered: bool = False
    forensics_completed: bool = True
    sound: bool = True


@dataclass
class SuspectsConfig:
    """Process suspect pattern configuration."""

    patterns: list[str] = field(
        default_factory=lambda: [
            "codemeter",
            "bitdefender",
            "biomesyncd",
            "motu",
            "coreaudiod",
            "kernel_task",
            "WindowServer",
        ]
    )


@dataclass
class Config:
    """Main configuration container."""

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    suspects: SuspectsConfig = field(default_factory=SuspectsConfig)
    learning_mode: bool = False

    @property
    def config_dir(self) -> Path:
        """Configuration directory."""
        return Path.home() / ".config" / "pause-monitor"

    @property
    def config_path(self) -> Path:
        """Path to config file."""
        return self.config_dir / "config.toml"

    @property
    def data_dir(self) -> Path:
        """Data directory."""
        return Path.home() / ".local" / "share" / "pause-monitor"

    @property
    def events_dir(self) -> Path:
        """Events directory for forensics."""
        return self.data_dir / "events"

    @property
    def db_path(self) -> Path:
        """Database path."""
        return self.data_dir / "data.db"

    @property
    def log_path(self) -> Path:
        """Daemon log path."""
        return self.data_dir / "daemon.log"

    @property
    def pid_path(self) -> Path:
        """PID file path."""
        return self.data_dir / "daemon.pid"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): add configuration dataclasses with defaults"
```

---

### Task 3: Config Load/Save

**Files:**
- Modify: `src/pause_monitor/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write failing tests for load/save**

Add to `tests/test_config.py`:

```python
def test_config_save_creates_file(tmp_path: Path):
    """Config.save() creates config file."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.save(config_path)
    assert config_path.exists()


def test_config_save_preserves_values(tmp_path: Path):
    """Config.save() writes correct TOML values."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sampling.normal_interval = 10
    config.learning_mode = True
    config.save(config_path)

    content = config_path.read_text()
    assert "normal_interval = 10" in content
    assert "learning_mode = true" in content


def test_config_load_reads_values(tmp_path: Path):
    """Config.load() reads values from file."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
learning_mode = true

[sampling]
normal_interval = 10
elevation_threshold = 50

[alerts]
enabled = false
""")

    config = Config.load(config_path)
    assert config.learning_mode is True
    assert config.sampling.normal_interval == 10
    assert config.sampling.elevation_threshold == 50
    assert config.sampling.elevated_interval == 1  # Default preserved
    assert config.alerts.enabled is False


def test_config_load_missing_file_returns_defaults(tmp_path: Path):
    """Config.load() returns defaults when file doesn't exist."""
    config_path = tmp_path / "nonexistent.toml"
    config = Config.load(config_path)
    assert config.sampling.normal_interval == 5
    assert config.learning_mode is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_save_creates_file -v`
Expected: FAIL with AttributeError (no save method)

**Step 3: Implement save method**

Add to `Config` class in `src/pause_monitor/config.py`:

```python
    def save(self, path: Path | None = None) -> None:
        """Save config to TOML file."""
        path = path or self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)

        doc = tomlkit.document()
        doc.add("learning_mode", self.learning_mode)
        doc.add(tomlkit.nl())

        sampling = tomlkit.table()
        sampling.add("normal_interval", self.sampling.normal_interval)
        sampling.add("elevated_interval", self.sampling.elevated_interval)
        sampling.add("elevation_threshold", self.sampling.elevation_threshold)
        sampling.add("critical_threshold", self.sampling.critical_threshold)
        doc.add("sampling", sampling)
        doc.add(tomlkit.nl())

        retention = tomlkit.table()
        retention.add("samples_days", self.retention.samples_days)
        retention.add("events_days", self.retention.events_days)
        doc.add("retention", retention)
        doc.add(tomlkit.nl())

        alerts = tomlkit.table()
        alerts.add("enabled", self.alerts.enabled)
        alerts.add("pause_detected", self.alerts.pause_detected)
        alerts.add("pause_min_duration", self.alerts.pause_min_duration)
        alerts.add("critical_stress", self.alerts.critical_stress)
        alerts.add("critical_threshold", self.alerts.critical_threshold)
        alerts.add("critical_duration", self.alerts.critical_duration)
        alerts.add("elevated_entered", self.alerts.elevated_entered)
        alerts.add("forensics_completed", self.alerts.forensics_completed)
        alerts.add("sound", self.alerts.sound)
        doc.add("alerts", alerts)
        doc.add(tomlkit.nl())

        suspects = tomlkit.table()
        suspects.add("patterns", self.suspects.patterns)
        doc.add("suspects", suspects)

        path.write_text(tomlkit.dumps(doc))
```

**Step 4: Implement load method**

Add to `Config` class:

```python
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from TOML file, returning defaults for missing values."""
        config = cls()
        path = path or config.config_path
        if not path.exists():
            return config

        with open(path) as f:
            data = tomlkit.load(f)

        sampling_data = data.get("sampling", {})
        retention_data = data.get("retention", {})
        alerts_data = data.get("alerts", {})
        suspects_data = data.get("suspects", {})

        return cls(
            sampling=SamplingConfig(
                normal_interval=sampling_data.get("normal_interval", 5),
                elevated_interval=sampling_data.get("elevated_interval", 1),
                elevation_threshold=sampling_data.get("elevation_threshold", 30),
                critical_threshold=sampling_data.get("critical_threshold", 60),
            ),
            retention=RetentionConfig(
                samples_days=retention_data.get("samples_days", 30),
                events_days=retention_data.get("events_days", 90),
            ),
            alerts=AlertsConfig(
                enabled=alerts_data.get("enabled", True),
                pause_detected=alerts_data.get("pause_detected", True),
                pause_min_duration=alerts_data.get("pause_min_duration", 2.0),
                critical_stress=alerts_data.get("critical_stress", True),
                critical_threshold=alerts_data.get("critical_threshold", 60),
                critical_duration=alerts_data.get("critical_duration", 30),
                elevated_entered=alerts_data.get("elevated_entered", False),
                forensics_completed=alerts_data.get("forensics_completed", True),
                sound=alerts_data.get("sound", True),
            ),
            suspects=SuspectsConfig(
                patterns=suspects_data.get(
                    "patterns",
                    [
                        "codemeter",
                        "bitdefender",
                        "biomesyncd",
                        "motu",
                        "coreaudiod",
                        "kernel_task",
                        "WindowServer",
                    ],
                ),
            ),
            learning_mode=data.get("learning_mode", False),
        )
```

**Step 5: Run all config tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (10 tests)

**Step 6: Commit**

```bash
git add src/pause_monitor/config.py tests/test_config.py
git commit -m "feat(config): add load/save with TOML serialization"
```

---

## Phase 2: Stress Detection

**IMPORTANT:** StressBreakdown must be defined BEFORE storage operations that use it.

### Task 4: StressBreakdown Dataclass

**Files:**
- Create: `src/pause_monitor/stress.py`
- Create: `tests/test_stress.py`

**Step 1: Write failing tests for StressBreakdown**

Create `tests/test_stress.py`:

```python
"""Tests for stress score calculation."""

import pytest

from pause_monitor.stress import StressBreakdown


def test_stress_breakdown_total():
    """StressBreakdown.total sums components."""
    breakdown = StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)
    assert breakdown.total == 15


def test_stress_breakdown_total_capped():
    """StressBreakdown.total capped at 100."""
    breakdown = StressBreakdown(load=40, memory=30, thermal=20, latency=30, io=20)
    assert breakdown.total == 100
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stress.py::test_stress_breakdown_total -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Create stress module with StressBreakdown**

Create `src/pause_monitor/stress.py`:

```python
"""Stress score calculation for pause-monitor."""

from dataclasses import dataclass


@dataclass
class StressBreakdown:
    """Per-factor stress scores.

    This is the CANONICAL definition - storage.py imports from here.
    """

    load: int      # 0-40: load/cores ratio
    memory: int    # 0-30: memory pressure
    thermal: int   # 0-20: throttling active
    latency: int   # 0-30: self-latency
    io: int        # 0-20: disk I/O spike

    @property
    def total(self) -> int:
        """Combined stress score, capped at 100."""
        return min(100, self.load + self.memory + self.thermal + self.latency + self.io)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): add StressBreakdown dataclass"
```

---

### Task 5: Stress Calculation Function

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Modify: `tests/test_stress.py`

**Step 1: Write failing tests for calculate_stress**

Add to `tests/test_stress.py`:

```python
from pause_monitor.stress import calculate_stress


def test_stress_zero_when_idle():
    """Idle system has zero stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.total == 0


def test_stress_load_contribution():
    """Load above cores contributes to stress."""
    breakdown = calculate_stress(
        load_avg=16.0,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.load == 20
    assert breakdown.total == 20


def test_stress_load_capped_at_40():
    """Load contribution capped at 40."""
    breakdown = calculate_stress(
        load_avg=40.0,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.load == 40


def test_stress_memory_contribution():
    """Low available memory contributes to stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=10.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.memory == 15


def test_stress_thermal_contribution():
    """Thermal throttling adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=True,
        latency_ratio=1.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.thermal == 20


def test_stress_latency_contribution():
    """High latency ratio contributes to stress."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=2.0,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.latency == 20


def test_stress_latency_only_above_threshold():
    """Latency only contributes if ratio > 1.5."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.4,
        io_rate=0,
        io_baseline=10_000_000,
    )
    assert breakdown.latency == 0


def test_stress_io_spike_contribution():
    """I/O spike (10x baseline) adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=150_000_000,
        io_baseline=10_000_000,
    )
    assert breakdown.io == 20


def test_stress_io_sustained_high():
    """Sustained high I/O (>100 MB/s) adds 20 points."""
    breakdown = calculate_stress(
        load_avg=0.5,
        core_count=8,
        mem_available_pct=80.0,
        throttled=False,
        latency_ratio=1.0,
        io_rate=150_000_000,
        io_baseline=100_000_000,
    )
    assert breakdown.io == 20
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stress.py::test_stress_zero_when_idle -v`
Expected: FAIL with ImportError

**Step 3: Implement calculate_stress**

Add to `src/pause_monitor/stress.py`:

```python
def calculate_stress(
    load_avg: float,
    core_count: int,
    mem_available_pct: float,
    throttled: bool | None,
    latency_ratio: float,
    io_rate: int,
    io_baseline: int,
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

    return StressBreakdown(
        load=load_score,
        memory=mem_score,
        thermal=thermal_score,
        latency=latency_score,
        io=io_score,
    )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS (12 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): implement multi-factor stress calculation"
```

---

### Task 6: Memory Pressure Detection

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Modify: `tests/test_stress.py`

**Step 1: Write failing tests for memory pressure**

Add to `tests/test_stress.py`:

```python
from pause_monitor.stress import get_memory_pressure_fast, MemoryPressureLevel


def test_memory_pressure_returns_level():
    """get_memory_pressure_fast returns valid percentage."""
    level = get_memory_pressure_fast()
    assert 0 <= level <= 100


def test_memory_pressure_level_enum():
    """MemoryPressureLevel categorizes correctly."""
    assert MemoryPressureLevel.from_percent(80) == MemoryPressureLevel.NORMAL
    assert MemoryPressureLevel.from_percent(35) == MemoryPressureLevel.WARNING
    assert MemoryPressureLevel.from_percent(10) == MemoryPressureLevel.CRITICAL
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stress.py::test_memory_pressure_returns_level -v`
Expected: FAIL with ImportError

**Step 3: Implement memory pressure detection**

Add to `src/pause_monitor/stress.py`:

```python
import ctypes
from enum import Enum


class MemoryPressureLevel(Enum):
    """Memory pressure categories."""

    NORMAL = "normal"      # >50% available
    WARNING = "warning"    # 20-50% available
    CRITICAL = "critical"  # <20% available

    @classmethod
    def from_percent(cls, available_pct: int) -> "MemoryPressureLevel":
        """Categorize memory pressure from availability percentage."""
        if available_pct > 50:
            return cls.NORMAL
        elif available_pct >= 20:
            return cls.WARNING
        else:
            return cls.CRITICAL


def get_memory_pressure_fast() -> int:
    """Get memory pressure level via sysctl (no subprocess).

    Returns:
        Percentage of memory "free" (0-100). Higher = more available.
    """
    libc = ctypes.CDLL("/usr/lib/libc.dylib")
    size = ctypes.c_size_t(4)
    level = ctypes.c_int()

    result = libc.sysctlbyname(
        b"kern.memorystatus_level",
        ctypes.byref(level),
        ctypes.byref(size),
        None,
        0,
    )

    if result != 0:
        return 50  # Fallback: assume moderate pressure

    return level.value
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS (14 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): add memory pressure detection via sysctl"
```

---

### Task 7: I/O Baseline Manager

**Files:**
- Modify: `src/pause_monitor/stress.py`
- Modify: `tests/test_stress.py`

**Step 1: Write failing tests for I/O baseline**

Add to `tests/test_stress.py`:

```python
from pause_monitor.stress import IOBaselineManager


def test_io_baseline_manager_initial_state():
    """IOBaselineManager starts with default baseline."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.baseline_fast == 10_000_000
    assert manager.learning is True


def test_io_baseline_manager_persisted():
    """IOBaselineManager uses persisted baseline if available."""
    manager = IOBaselineManager(persisted_baseline=50_000_000)
    assert manager.baseline_fast == 50_000_000
    assert manager.learning is False


def test_io_baseline_manager_update():
    """IOBaselineManager updates baseline with EMA."""
    manager = IOBaselineManager(persisted_baseline=10_000_000)
    manager.update(20_000_000)
    assert 10_900_000 < manager.baseline_fast < 11_100_000


def test_io_baseline_manager_learning_completes():
    """IOBaselineManager exits learning after enough samples."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.learning is True

    for _ in range(60):
        manager.update(10_000_000)

    assert manager.learning is False


def test_io_baseline_manager_spike_detection():
    """IOBaselineManager detects spikes correctly."""
    manager = IOBaselineManager(persisted_baseline=10_000_000)

    assert manager.is_spike(50_000_000) is False  # 5x, not spike
    assert manager.is_spike(110_000_000) is True  # 11x, spike


def test_io_baseline_manager_learning_spike_threshold():
    """During learning, only extreme absolute values are spikes."""
    manager = IOBaselineManager(persisted_baseline=None)
    assert manager.learning is True

    assert manager.is_spike(150_000_000) is False
    assert manager.is_spike(250_000_000) is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stress.py::test_io_baseline_manager_initial_state -v`
Expected: FAIL with ImportError

**Step 3: Implement IOBaselineManager**

Add to `src/pause_monitor/stress.py`:

```python
import structlog

log = structlog.get_logger()


class IOBaselineManager:
    """Manage I/O baseline with learning period awareness."""

    LEARNING_SAMPLES = 60  # ~1 minute at 1s sampling
    DEFAULT_BASELINE = 10_000_000  # 10 MB/s

    def __init__(self, persisted_baseline: float | None):
        self.baseline_fast = persisted_baseline or self.DEFAULT_BASELINE
        self.baseline_slow = persisted_baseline or self.DEFAULT_BASELINE
        self.samples_seen = 0 if persisted_baseline is None else self.LEARNING_SAMPLES
        self.learning = self.samples_seen < self.LEARNING_SAMPLES

    def update(self, io_rate: float) -> None:
        """Update baselines with new I/O rate observation."""
        self.samples_seen += 1

        if self.learning:
            alpha_fast = 0.3
            alpha_slow = 0.1

            if self.samples_seen >= self.LEARNING_SAMPLES:
                self.learning = False
                log.info(
                    "io_baseline_learning_complete",
                    baseline_fast=self.baseline_fast,
                    baseline_slow=self.baseline_slow,
                )
        else:
            alpha_fast = 0.1
            alpha_slow = 0.001

        self.baseline_fast = alpha_fast * io_rate + (1 - alpha_fast) * self.baseline_fast
        self.baseline_slow = alpha_slow * io_rate + (1 - alpha_slow) * self.baseline_slow

    def is_spike(self, io_rate: float) -> bool:
        """Check if current I/O rate is a spike relative to baseline."""
        if self.learning:
            return io_rate > 200_000_000  # 200 MB/s absolute during learning

        return io_rate > self.baseline_fast * 10
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_stress.py -v`
Expected: PASS (20 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/stress.py tests/test_stress.py
git commit -m "feat(stress): add IOBaselineManager with learning period"
```

---

> **Next:** [Part 2: Storage](./02-storage.md)
