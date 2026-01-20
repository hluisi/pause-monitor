# pause-monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a real-time macOS system health monitor that identifies the root cause of intermittent system pauses through multi-factor stress detection, adaptive sampling, and automated forensics capture.

**Architecture:** A daemon continuously streams metrics from `powermetrics` (privileged), calculates a composite stress score, and stores samples in SQLite. When stress exceeds thresholds, sampling intensifies. When pauses are detected (via monotonic clock drift), forensics captures (spindump, tailspin, logs) are triggered. A Textual TUI provides real-time visualization; CLI commands enable querying history.

**Tech Stack:** Python 3.11+, SQLite (WAL mode), powermetrics (macOS), Textual (TUI), Click (CLI), structlog (logging), tomlkit (config)

**Source Design:** `docs/plans/2026-01-20-pause-monitor-design.md`

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

## Phase 3: Test Infrastructure

### Task 8: Shared Test Fixtures

**Files:**
- Create: `tests/conftest.py`

**Step 1: Create conftest.py with fixtures**

Create `tests/conftest.py`:

```python
"""Shared test fixtures for pause-monitor."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from pause_monitor.stress import StressBreakdown


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(tmp_db: Path) -> Path:
    """Create an initialized database with schema."""
    from pause_monitor.storage import init_database
    init_database(tmp_db)
    return tmp_db


def create_test_stress() -> StressBreakdown:
    """Create a StressBreakdown for testing."""
    return StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0)


@pytest.fixture
def sample_stress() -> StressBreakdown:
    """Fixture for a sample StressBreakdown."""
    return create_test_stress()
```

**Step 2: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add shared fixtures in conftest.py"
```

---

## Phase 4: Storage Layer

### Task 9: Database Schema

**Files:**
- Create: `src/pause_monitor/storage.py`
- Create: `tests/test_storage.py`

**Step 1: Write failing test for database initialization**

Create `tests/test_storage.py`:

```python
"""Tests for SQLite storage layer."""

import sqlite3
from pathlib import Path

import pytest

from pause_monitor.storage import init_database, get_schema_version, SCHEMA_VERSION


def test_init_database_creates_file(tmp_db: Path):
    """init_database creates SQLite file."""
    init_database(tmp_db)
    assert tmp_db.exists()


def test_init_database_enables_wal(tmp_db: Path):
    """init_database enables WAL journal mode."""
    init_database(tmp_db)

    conn = sqlite3.connect(tmp_db)
    result = conn.execute("PRAGMA journal_mode").fetchone()
    conn.close()
    assert result[0] == "wal"


def test_init_database_creates_tables(tmp_db: Path):
    """init_database creates required tables."""
    init_database(tmp_db)

    conn = sqlite3.connect(tmp_db)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()

    table_names = [t[0] for t in tables]
    assert "samples" in table_names
    assert "process_samples" in table_names
    assert "events" in table_names
    assert "daemon_state" in table_names


def test_init_database_sets_schema_version(tmp_db: Path):
    """init_database sets schema version in daemon_state."""
    init_database(tmp_db)

    conn = sqlite3.connect(tmp_db)
    version = get_schema_version(conn)
    conn.close()
    assert version == SCHEMA_VERSION
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_init_database_creates_file -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement storage module with schema**

Create `src/pause_monitor/storage.py`:

```python
"""SQLite storage layer for pause-monitor."""

import sqlite3
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 1

SCHEMA = """
-- Periodic samples (one row per sample interval)
CREATE TABLE IF NOT EXISTS samples (
    id              INTEGER PRIMARY KEY,
    timestamp       REAL NOT NULL,
    interval        REAL NOT NULL,
    cpu_pct         REAL,
    load_avg        REAL,
    mem_available   INTEGER,
    swap_used       INTEGER,
    io_read         INTEGER,
    io_write        INTEGER,
    net_sent        INTEGER,
    net_recv        INTEGER,
    cpu_temp        REAL,
    cpu_freq        INTEGER,
    throttled       INTEGER,
    gpu_pct         REAL,
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON samples(timestamp);

-- Per-process snapshots (linked to samples)
CREATE TABLE IF NOT EXISTS process_samples (
    id              INTEGER PRIMARY KEY,
    sample_id       INTEGER NOT NULL REFERENCES samples(id),
    pid             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    cpu_pct         REAL,
    mem_pct         REAL,
    io_read         INTEGER,
    io_write        INTEGER,
    energy_impact   REAL,
    is_suspect      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_process_samples_sample_id ON process_samples(sample_id);

-- Pause events
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    timestamp       REAL NOT NULL,
    duration        REAL NOT NULL,
    stress_total    INTEGER,
    stress_load     INTEGER,
    stress_memory   INTEGER,
    stress_thermal  INTEGER,
    stress_latency  INTEGER,
    stress_io       INTEGER,
    culprits        TEXT,
    event_dir       TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- Daemon state (persisted across restarts)
CREATE TABLE IF NOT EXISTS daemon_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      REAL NOT NULL
);
"""


def init_database(db_path: Path) -> None:
    """Initialize database with WAL mode and schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # WAL mode for concurrent reads
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA journal_size_limit=16777216")
        conn.execute("PRAGMA foreign_keys=ON")

        # Create schema
        conn.executescript(SCHEMA)

        # Set schema version
        conn.execute(
            "INSERT OR REPLACE INTO daemon_state (key, value, updated_at) VALUES (?, ?, ?)",
            ("schema_version", str(SCHEMA_VERSION), time.time()),
        )
        conn.commit()
        log.info("database_initialized", path=str(db_path), version=SCHEMA_VERSION)
    finally:
        conn.close()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    try:
        row = conn.execute(
            "SELECT value FROM daemon_state WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add SQLite schema and initialization"
```

---

### Task 10: Storage Sample Operations

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write failing tests for Sample dataclass and insert**

Add to `tests/test_storage.py`:

```python
from datetime import datetime
from conftest import create_test_stress


def test_sample_dataclass_fields():
    """Sample has correct fields matching design doc."""
    from pause_monitor.storage import Sample
    from pause_monitor.stress import StressBreakdown

    sample = Sample(
        timestamp=datetime.now(),
        interval=5.0,
        cpu_pct=25.5,
        load_avg=1.5,
        mem_available=8_000_000_000,
        swap_used=100_000_000,
        io_read=1_000_000,
        io_write=500_000,
        net_sent=10_000,
        net_recv=20_000,
        cpu_temp=65.0,
        cpu_freq=3000,
        throttled=False,
        gpu_pct=10.0,
        stress=StressBreakdown(load=10, memory=5, thermal=0, latency=0, io=0),
    )
    assert sample.cpu_pct == 25.5
    assert sample.stress.total == 15


def test_insert_sample(initialized_db: Path, sample_stress):
    """insert_sample stores sample in database."""
    from pause_monitor.storage import Sample, insert_sample

    sample = Sample(
        timestamp=datetime.now(),
        interval=5.0,
        cpu_pct=25.5,
        load_avg=1.5,
        mem_available=8_000_000_000,
        swap_used=100_000_000,
        io_read=1_000_000,
        io_write=500_000,
        net_sent=10_000,
        net_recv=20_000,
        cpu_temp=65.0,
        cpu_freq=3000,
        throttled=False,
        gpu_pct=10.0,
        stress=sample_stress,
    )

    conn = sqlite3.connect(initialized_db)
    sample_id = insert_sample(conn, sample)
    conn.close()

    assert sample_id > 0


def test_get_recent_samples(initialized_db: Path, sample_stress):
    """get_recent_samples returns samples in reverse chronological order."""
    from pause_monitor.storage import Sample, insert_sample, get_recent_samples
    import time

    conn = sqlite3.connect(initialized_db)

    for i in range(5):
        sample = Sample(
            timestamp=datetime.fromtimestamp(1000000 + i * 5),
            interval=5.0,
            cpu_pct=10.0 + i,
            load_avg=1.0,
            mem_available=8_000_000_000,
            swap_used=0,
            io_read=0,
            io_write=0,
            net_sent=0,
            net_recv=0,
            cpu_temp=None,
            cpu_freq=None,
            throttled=None,
            gpu_pct=None,
            stress=sample_stress,
        )
        insert_sample(conn, sample)

    samples = get_recent_samples(conn, limit=3)
    conn.close()

    assert len(samples) == 3
    assert samples[0].cpu_pct == 14.0  # Most recent
    assert samples[2].cpu_pct == 12.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_sample_dataclass_fields -v`
Expected: FAIL with ImportError

**Step 3: Implement Sample dataclass and operations**

Add to `src/pause_monitor/storage.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from pause_monitor.stress import StressBreakdown


@dataclass
class Sample:
    """Single metrics sample.

    Field names match design doc exactly.
    """

    timestamp: datetime
    interval: float
    cpu_pct: float | None
    load_avg: float | None
    mem_available: int | None
    swap_used: int | None
    io_read: int | None
    io_write: int | None
    net_sent: int | None
    net_recv: int | None
    cpu_temp: float | None
    cpu_freq: int | None
    throttled: bool | None
    gpu_pct: float | None
    stress: StressBreakdown


def insert_sample(conn: sqlite3.Connection, sample: Sample) -> int:
    """Insert a sample and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO samples (
            timestamp, interval, cpu_pct, load_avg, mem_available, swap_used,
            io_read, io_write, net_sent, net_recv, cpu_temp, cpu_freq,
            throttled, gpu_pct, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.timestamp.timestamp(),
            sample.interval,
            sample.cpu_pct,
            sample.load_avg,
            sample.mem_available,
            sample.swap_used,
            sample.io_read,
            sample.io_write,
            sample.net_sent,
            sample.net_recv,
            sample.cpu_temp,
            sample.cpu_freq,
            int(sample.throttled) if sample.throttled is not None else None,
            sample.gpu_pct,
            sample.stress.total,
            sample.stress.load,
            sample.stress.memory,
            sample.stress.thermal,
            sample.stress.latency,
            sample.stress.io,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_samples(conn: sqlite3.Connection, limit: int = 100) -> list[Sample]:
    """Get most recent samples."""
    rows = conn.execute(
        """
        SELECT timestamp, interval, cpu_pct, load_avg, mem_available, swap_used,
               io_read, io_write, net_sent, net_recv, cpu_temp, cpu_freq,
               throttled, gpu_pct, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io
        FROM samples ORDER BY timestamp DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        Sample(
            timestamp=datetime.fromtimestamp(row[0]),
            interval=row[1],
            cpu_pct=row[2],
            load_avg=row[3],
            mem_available=row[4],
            swap_used=row[5],
            io_read=row[6],
            io_write=row[7],
            net_sent=row[8],
            net_recv=row[9],
            cpu_temp=row[10],
            cpu_freq=row[11],
            throttled=bool(row[12]) if row[12] is not None else None,
            gpu_pct=row[13],
            stress=StressBreakdown(
                load=row[15] or 0,
                memory=row[16] or 0,
                thermal=row[17] or 0,
                latency=row[18] or 0,
                io=row[19] or 0,
            ),
        )
        for row in rows
    ]
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add Sample dataclass and insert/query operations"
```

---

### Task 11: Storage Event Operations

**Files:**
- Modify: `src/pause_monitor/storage.py`
- Modify: `tests/test_storage.py`

**Step 1: Write failing tests for Event operations**

Add to `tests/test_storage.py`:

```python
def test_event_dataclass():
    """Event has correct fields."""
    from pause_monitor.storage import Event

    event = Event(
        timestamp=datetime.now(),
        duration=3.5,
        stress=create_test_stress(),
        culprits=["codemeter", "WindowServer"],
        event_dir="/path/to/events/12345",
        notes="Test pause",
    )
    assert event.duration == 3.5
    assert "codemeter" in event.culprits


def test_insert_event(initialized_db: Path, sample_stress):
    """insert_event stores event in database."""
    from pause_monitor.storage import Event, insert_event

    event = Event(
        timestamp=datetime.now(),
        duration=2.5,
        stress=sample_stress,
        culprits=["test_process"],
        event_dir=None,
        notes=None,
    )

    conn = sqlite3.connect(initialized_db)
    event_id = insert_event(conn, event)
    conn.close()

    assert event_id > 0


def test_get_events_by_timerange(initialized_db: Path, sample_stress):
    """get_events returns events within time range."""
    from pause_monitor.storage import Event, insert_event, get_events

    conn = sqlite3.connect(initialized_db)

    base_time = 1000000.0
    for i in range(5):
        event = Event(
            timestamp=datetime.fromtimestamp(base_time + i * 3600),
            duration=1.0 + i,
            stress=sample_stress,
            culprits=[],
            event_dir=None,
            notes=None,
        )
        insert_event(conn, event)

    events = get_events(
        conn,
        start=datetime.fromtimestamp(base_time + 3600),
        end=datetime.fromtimestamp(base_time + 10800),
    )
    conn.close()

    assert len(events) == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::test_event_dataclass -v`
Expected: FAIL with ImportError

**Step 3: Implement Event dataclass and operations**

Add to `src/pause_monitor/storage.py`:

```python
import json


@dataclass
class Event:
    """Pause event record."""

    timestamp: datetime
    duration: float
    stress: StressBreakdown
    culprits: list[str]
    event_dir: str | None
    notes: str | None
    id: int | None = None


def insert_event(conn: sqlite3.Connection, event: Event) -> int:
    """Insert an event and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO events (
            timestamp, duration, stress_total, stress_load, stress_memory,
            stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.timestamp.timestamp(),
            event.duration,
            event.stress.total,
            event.stress.load,
            event.stress.memory,
            event.stress.thermal,
            event.stress.latency,
            event.stress.io,
            json.dumps(event.culprits),
            event.event_dir,
            event.notes,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_events(
    conn: sqlite3.Connection,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> list[Event]:
    """Get events, optionally filtered by time range."""
    query = """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        FROM events
    """
    params: list = []

    if start or end:
        query += " WHERE "
        conditions = []
        if start:
            conditions.append("timestamp >= ?")
            params.append(start.timestamp())
        if end:
            conditions.append("timestamp <= ?")
            params.append(end.timestamp())
        query += " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        Event(
            id=row[0],
            timestamp=datetime.fromtimestamp(row[1]),
            duration=row[2],
            stress=StressBreakdown(
                load=row[4] or 0,
                memory=row[5] or 0,
                thermal=row[6] or 0,
                latency=row[7] or 0,
                io=row[8] or 0,
            ),
            culprits=json.loads(row[9]) if row[9] else [],
            event_dir=row[10],
            notes=row[11],
        )
        for row in rows
    ]


def get_event_by_id(conn: sqlite3.Connection, event_id: int) -> Event | None:
    """Get a single event by ID."""
    row = conn.execute(
        """
        SELECT id, timestamp, duration, stress_total, stress_load, stress_memory,
               stress_thermal, stress_latency, stress_io, culprits, event_dir, notes
        FROM events WHERE id = ?
        """,
        (event_id,),
    ).fetchone()

    if not row:
        return None

    return Event(
        id=row[0],
        timestamp=datetime.fromtimestamp(row[1]),
        duration=row[2],
        stress=StressBreakdown(
            load=row[4] or 0,
            memory=row[5] or 0,
            thermal=row[6] or 0,
            latency=row[7] or 0,
            io=row[8] or 0,
        ),
        culprits=json.loads(row[9]) if row[9] else [],
        event_dir=row[10],
        notes=row[11],
    )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/storage.py tests/test_storage.py
git commit -m "feat(storage): add Event dataclass and query operations"
```

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

        Handles plist streaming where multiple plists are concatenated.
        """
        if self._process is None or self._process.stdout is None:
            return

        async for chunk in self._process.stdout:
            self._buffer += chunk

            # Split on plist boundaries (each starts with '<?xml' or 'bplist')
            while True:
                # Look for binary plist header
                bplist_idx = self._buffer.find(b"bplist", 1)
                # Look for XML plist header
                xml_idx = self._buffer.find(b"<?xml", 1)

                # Find the earliest boundary
                next_start = -1
                if bplist_idx > 0 and xml_idx > 0:
                    next_start = min(bplist_idx, xml_idx)
                elif bplist_idx > 0:
                    next_start = bplist_idx
                elif xml_idx > 0:
                    next_start = xml_idx

                if next_start > 0:
                    # Extract complete plist
                    plist_data = self._buffer[:next_start]
                    self._buffer = self._buffer[next_start:]

                    result = parse_powermetrics_sample(plist_data)
                    yield result
                else:
                    break
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
    """SamplePolicy returns to normal when stress drops."""
    policy = SamplePolicy(
        normal_interval=5,
        elevated_interval=1,
        elevation_threshold=30,
        cooldown_samples=3,
    )

    # Elevate
    high_stress = StressBreakdown(load=40, memory=0, thermal=0, latency=0, io=0)
    policy.update(high_stress)
    assert policy.state == SamplingState.ELEVATED

    # Drop below threshold for cooldown period
    low_stress = StressBreakdown(load=5, memory=0, thermal=0, latency=0, io=0)
    for _ in range(3):
        policy.update(low_stress)

    assert policy.state == SamplingState.NORMAL


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
    """Adaptive sampling policy based on stress levels."""

    def __init__(
        self,
        normal_interval: int = 5,
        elevated_interval: int = 1,
        elevation_threshold: int = 30,
        critical_threshold: int = 60,
        cooldown_samples: int = 5,
    ):
        self.normal_interval = normal_interval
        self.elevated_interval = elevated_interval
        self.elevation_threshold = elevation_threshold
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

        # State transitions
        old_state = self._state

        if total >= self.elevation_threshold:
            self._state = SamplingState.ELEVATED
            self._samples_below_threshold = 0
        else:
            self._samples_below_threshold += 1
            if self._samples_below_threshold >= self.cooldown_samples:
                self._state = SamplingState.NORMAL

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
Expected: PASS (14 tests)

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

## Phase 7: Forensics Capture

### Task 18: Forensics Directory Structure

**Files:**
- Create: `src/pause_monitor/forensics.py`
- Create: `tests/test_forensics.py`

**Step 1: Write failing tests for forensics structure**

Create `tests/test_forensics.py`:

```python
"""Tests for forensics capture."""

from pathlib import Path
from datetime import datetime

import pytest

from pause_monitor.forensics import ForensicsCapture, create_event_dir


def test_create_event_dir(tmp_path: Path):
    """create_event_dir creates timestamped directory."""
    events_dir = tmp_path / "events"
    event_time = datetime(2024, 1, 15, 10, 30, 45)

    event_dir = create_event_dir(events_dir, event_time)

    assert event_dir.exists()
    assert "2024-01-15" in event_dir.name
    assert "10-30-45" in event_dir.name


def test_forensics_capture_creates_files(tmp_path: Path):
    """ForensicsCapture creates expected files."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    # Write test data
    capture.write_metadata({"timestamp": 1705323045, "duration": 3.5})

    assert (event_dir / "metadata.json").exists()


def test_forensics_capture_writes_process_snapshot(tmp_path: Path):
    """ForensicsCapture writes process snapshot."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    processes = [
        {"pid": 123, "name": "codemeter", "cpu": 50.0},
        {"pid": 456, "name": "python", "cpu": 10.0},
    ]
    capture.write_process_snapshot(processes)

    assert (event_dir / "processes.json").exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_forensics.py::test_create_event_dir -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement forensics structure**

Create `src/pause_monitor/forensics.py`:

```python
"""Forensics capture for pause events."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_forensics.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/forensics.py tests/test_forensics.py
git commit -m "feat(forensics): add event directory and capture structure"
```

---

### Task 19: Forensics Capture Commands (spindump, tailspin, logs)

**Files:**
- Modify: `src/pause_monitor/forensics.py`
- Modify: `tests/test_forensics.py`

**Step 1: Write failing tests for capture commands**

Add to `tests/test_forensics.py`:

```python
import asyncio
from unittest.mock import patch, AsyncMock

from pause_monitor.forensics import (
    capture_spindump,
    capture_tailspin,
    capture_system_logs,
    run_full_capture,
)


@pytest.mark.asyncio
async def test_capture_spindump_creates_file(tmp_path: Path):
    """capture_spindump creates spindump output file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"spindump output", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        success = await capture_spindump(event_dir)

        assert success is True
        # Verify spindump was called
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "spindump" in call_args[0]


@pytest.mark.asyncio
async def test_capture_tailspin_creates_file(tmp_path: Path):
    """capture_tailspin creates tailspin output file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        success = await capture_tailspin(event_dir)

        assert success is True
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "tailspin" in call_args[0]


@pytest.mark.asyncio
async def test_capture_system_logs_creates_file(tmp_path: Path):
    """capture_system_logs creates filtered log file."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"log output here", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        success = await capture_system_logs(event_dir, window_seconds=60)

        assert success is True
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "log" in call_args[0]


@pytest.mark.asyncio
async def test_run_full_capture_orchestrates_all(tmp_path: Path):
    """run_full_capture runs all capture steps."""
    event_dir = tmp_path / "event_001"
    event_dir.mkdir()

    capture = ForensicsCapture(event_dir)

    with patch("pause_monitor.forensics.capture_spindump") as mock_spin:
        with patch("pause_monitor.forensics.capture_tailspin") as mock_tail:
            with patch("pause_monitor.forensics.capture_system_logs") as mock_logs:
                mock_spin.return_value = True
                mock_tail.return_value = True
                mock_logs.return_value = True

                await run_full_capture(capture, window_seconds=60)

                mock_spin.assert_called_once()
                mock_tail.assert_called_once()
                mock_logs.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_forensics.py::test_capture_spindump_creates_file -v`
Expected: FAIL with ImportError

**Step 3: Implement capture commands**

Add to `src/pause_monitor/forensics.py`:

```python
import asyncio
from datetime import timedelta


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
            "-o", str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await asyncio.wait_for(process.wait(), timeout=timeout)

        if output_path.exists():
            log.info("tailspin_captured", path=str(output_path))
            return True
        return False

    except asyncio.TimeoutError:
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
            "--last", f"{window_seconds}s",
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_forensics.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/forensics.py tests/test_forensics.py
git commit -m "feat(forensics): add spindump, tailspin, and log capture"
```

---

## Phase 8: Notifications

### Task 20: macOS Notification System

**Files:**
- Create: `src/pause_monitor/notifications.py`
- Create: `tests/test_notifications.py`

**Step 1: Write failing tests for notifications**

Create `tests/test_notifications.py`:

```python
"""Tests for notification system."""

from unittest.mock import patch, AsyncMock
from pathlib import Path

import pytest

from pause_monitor.notifications import (
    Notifier,
    NotificationType,
    send_notification,
)
from pause_monitor.config import AlertsConfig


def test_notifier_respects_enabled_flag():
    """Notifier does nothing when disabled."""
    config = AlertsConfig(enabled=False)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=5.0, event_dir=None)
        mock_send.assert_not_called()


def test_notifier_sends_pause_notification():
    """Notifier sends notification on pause detection."""
    config = AlertsConfig(enabled=True, pause_detected=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=5.0, event_dir=Path("/tmp/event"))

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "pause" in call_args.kwargs["title"].lower()


def test_notifier_respects_min_duration():
    """Notifier ignores pauses below minimum duration."""
    config = AlertsConfig(enabled=True, pause_detected=True, pause_min_duration=3.0)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.pause_detected(duration=2.0, event_dir=None)
        mock_send.assert_not_called()

        notifier.pause_detected(duration=3.5, event_dir=None)
        mock_send.assert_called_once()


def test_notifier_critical_stress():
    """Notifier sends critical stress notification."""
    config = AlertsConfig(enabled=True, critical_stress=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.critical_stress(stress_total=75, duration=60)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "critical" in call_args.kwargs["title"].lower()


def test_notifier_forensics_completed():
    """Notifier sends forensics completion notification."""
    config = AlertsConfig(enabled=True, forensics_completed=True)
    notifier = Notifier(config)

    with patch("pause_monitor.notifications.send_notification") as mock_send:
        notifier.forensics_completed(event_dir=Path("/tmp/event"))

        mock_send.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_notifications.py::test_notifier_respects_enabled_flag -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement notifications**

Create `src/pause_monitor/notifications.py`:

```python
"""macOS notification system for pause-monitor."""

import subprocess
from enum import Enum
from pathlib import Path

import structlog

from pause_monitor.config import AlertsConfig

log = structlog.get_logger()


class NotificationType(Enum):
    """Types of notifications."""

    PAUSE_DETECTED = "pause_detected"
    CRITICAL_STRESS = "critical_stress"
    ELEVATED_ENTERED = "elevated_entered"
    FORENSICS_COMPLETED = "forensics_completed"


def send_notification(
    title: str,
    message: str,
    sound: bool = True,
    subtitle: str | None = None,
) -> bool:
    """Send a macOS notification via osascript.

    Args:
        title: Notification title
        message: Notification body
        sound: Whether to play default sound
        subtitle: Optional subtitle

    Returns:
        True if notification was sent successfully
    """
    sound_part = 'sound name "Funk"' if sound else ""
    subtitle_part = f'subtitle "{subtitle}"' if subtitle else ""

    script = f'''
    display notification "{message}" with title "{title}" {subtitle_part} {sound_part}
    '''

    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
        log.debug("notification_sent", title=title)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("notification_failed", error=str(e))
        return False


class Notifier:
    """Manages notifications based on alert configuration."""

    def __init__(self, config: AlertsConfig):
        self.config = config
        self._critical_start_time: float | None = None

    def pause_detected(self, duration: float, event_dir: Path | None) -> None:
        """Notify about detected pause."""
        if not self.config.enabled or not self.config.pause_detected:
            return

        if duration < self.config.pause_min_duration:
            return

        message = f"System was unresponsive for {duration:.1f}s"
        if event_dir:
            message += f"\nForensics: {event_dir.name}"

        send_notification(
            title="Pause Detected",
            message=message,
            sound=self.config.sound,
        )

    def critical_stress(self, stress_total: int, duration: float) -> None:
        """Notify about sustained critical stress."""
        if not self.config.enabled or not self.config.critical_stress:
            return

        if duration < self.config.critical_duration:
            return

        send_notification(
            title="Critical System Stress",
            message=f"Stress score {stress_total} for {duration:.0f}s",
            sound=self.config.sound,
        )

    def elevated_entered(self, stress_total: int) -> None:
        """Notify about entering elevated monitoring."""
        if not self.config.enabled or not self.config.elevated_entered:
            return

        send_notification(
            title="Elevated Monitoring",
            message=f"Stress score {stress_total} - sampling increased",
            sound=self.config.sound,
        )

    def forensics_completed(self, event_dir: Path) -> None:
        """Notify that forensics capture completed."""
        if not self.config.enabled or not self.config.forensics_completed:
            return

        send_notification(
            title="Forensics Capture Complete",
            message=f"Saved to {event_dir.name}",
            sound=self.config.sound,
        )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_notifications.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/notifications.py tests/test_notifications.py
git commit -m "feat(notifications): add macOS notification system"
```

---

## Phase 9: Daemon Core

### Task 21: Daemon State Management

**Files:**
- Create: `src/pause_monitor/daemon.py`
- Create: `tests/test_daemon.py`

**Step 1: Write failing tests for daemon state**

Create `tests/test_daemon.py`:

```python
"""Tests for daemon core."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from pause_monitor.daemon import DaemonState, Daemon
from pause_monitor.config import Config


def test_daemon_state_initial():
    """DaemonState initializes with correct defaults."""
    state = DaemonState()

    assert state.running is False
    assert state.sample_count == 0
    assert state.last_sample_time is None
    assert state.current_stress == 0


def test_daemon_state_update_sample():
    """DaemonState updates on new sample."""
    state = DaemonState()

    state.update_sample(stress=25, timestamp=datetime.now())

    assert state.sample_count == 1
    assert state.current_stress == 25
    assert state.last_sample_time is not None


def test_daemon_state_elevated_duration():
    """DaemonState tracks elevated duration."""
    state = DaemonState()

    state.enter_elevated()
    assert state.elevated_since is not None

    duration = state.elevated_duration
    assert duration >= 0


def test_daemon_init_creates_components(tmp_path: Path):
    """Daemon initializes all required components."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                assert daemon.config is config
                assert daemon.state is not None
                assert daemon.policy is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_state_initial -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement daemon state and initialization**

Create `src/pause_monitor/daemon.py`:

```python
"""Background daemon for pause-monitor."""

import asyncio
import signal
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

from pause_monitor.collector import (
    PowermetricsStream,
    SamplePolicy,
    get_core_count,
    get_system_metrics,
)
from pause_monitor.config import Config
from pause_monitor.forensics import ForensicsCapture, create_event_dir, run_full_capture
from pause_monitor.notifications import Notifier
from pause_monitor.sleepwake import PauseDetector, was_recently_asleep
from pause_monitor.storage import Sample, Event, init_database, insert_sample, insert_event
from pause_monitor.stress import (
    StressBreakdown,
    IOBaselineManager,
    calculate_stress,
    get_memory_pressure_fast,
)

log = structlog.get_logger()


@dataclass
class DaemonState:
    """Runtime state of the daemon."""

    running: bool = False
    sample_count: int = 0
    event_count: int = 0
    last_sample_time: datetime | None = None
    current_stress: int = 0
    elevated_since: datetime | None = None
    critical_since: datetime | None = None

    def update_sample(self, stress: int, timestamp: datetime) -> None:
        """Update state after a sample."""
        self.sample_count += 1
        self.current_stress = stress
        self.last_sample_time = timestamp

    def enter_elevated(self) -> None:
        """Mark entering elevated state."""
        if self.elevated_since is None:
            self.elevated_since = datetime.now()

    def exit_elevated(self) -> None:
        """Mark exiting elevated state."""
        self.elevated_since = None

    def enter_critical(self) -> None:
        """Mark entering critical state."""
        if self.critical_since is None:
            self.critical_since = datetime.now()

    def exit_critical(self) -> None:
        """Mark exiting critical state."""
        self.critical_since = None

    @property
    def elevated_duration(self) -> float:
        """Seconds in elevated state."""
        if self.elevated_since is None:
            return 0.0
        return (datetime.now() - self.elevated_since).total_seconds()

    @property
    def critical_duration(self) -> float:
        """Seconds in critical state."""
        if self.critical_since is None:
            return 0.0
        return (datetime.now() - self.critical_since).total_seconds()


class Daemon:
    """Main daemon class orchestrating sampling and detection."""

    def __init__(self, config: Config):
        self.config = config
        self.state = DaemonState()

        # Initialize components
        self.policy = SamplePolicy(
            normal_interval=config.sampling.normal_interval,
            elevated_interval=config.sampling.elevated_interval,
            elevation_threshold=config.sampling.elevation_threshold,
            critical_threshold=config.sampling.critical_threshold,
        )

        self.notifier = Notifier(config.alerts)
        self.io_baseline = IOBaselineManager(persisted_baseline=None)
        self.pause_detector = PauseDetector(
            expected_interval=config.sampling.normal_interval,
        )
        self.core_count = get_core_count()

        # Will be initialized on start
        self._conn: sqlite3.Connection | None = None
        self._powermetrics: PowermetricsStream | None = None
        self._caffeinate_proc: asyncio.subprocess.Process | None = None
        self._shutdown_event = asyncio.Event()
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add daemon state and initialization"
```

---

### Task 22: Daemon Lifecycle (start, stop, signal handlers)

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write failing tests for lifecycle**

Add to `tests/test_daemon.py`:

```python
@pytest.mark.asyncio
async def test_daemon_start_initializes_database(tmp_path: Path):
    """Daemon.start() initializes database."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                with patch.object(daemon, "_run_loop", new_callable=AsyncMock):
                    # Start and immediately stop
                    daemon._shutdown_event.set()
                    await daemon.start()

                    assert (tmp_path / "test.db").exists()


@pytest.mark.asyncio
async def test_daemon_stop_cleans_up(tmp_path: Path):
    """Daemon.stop() cleans up resources."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                # Mock powermetrics
                daemon._powermetrics = AsyncMock()
                daemon._powermetrics.stop = AsyncMock()

                await daemon.stop()

                daemon._powermetrics.stop.assert_called_once()
                assert daemon.state.running is False


@pytest.mark.asyncio
async def test_daemon_handles_sigterm(tmp_path: Path):
    """Daemon handles SIGTERM gracefully."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                # Trigger SIGTERM handler
                daemon._handle_signal(signal.SIGTERM)

                assert daemon._shutdown_event.is_set()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_start_initializes_database -v`
Expected: FAIL with AttributeError

**Step 3: Implement lifecycle methods**

Add to `Daemon` class in `src/pause_monitor/daemon.py`:

```python
    async def start(self) -> None:
        """Start the daemon."""
        log.info("daemon_starting")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        # Initialize database
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        init_database(self.config.db_path)
        self._conn = sqlite3.connect(self.config.db_path)

        # Start caffeinate to prevent App Nap
        await self._start_caffeinate()

        # Start powermetrics stream
        self._powermetrics = PowermetricsStream(
            interval_ms=self.policy.current_interval * 1000
        )

        self.state.running = True
        log.info("daemon_started")

        # Main loop
        await self._run_loop()

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        log.info("daemon_stopping")
        self.state.running = False

        # Stop powermetrics
        if self._powermetrics:
            await self._powermetrics.stop()
            self._powermetrics = None

        # Stop caffeinate
        await self._stop_caffeinate()

        # Close database
        if self._conn:
            self._conn.close()
            self._conn = None

        log.info("daemon_stopped")

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signals."""
        log.info("signal_received", signal=sig.name)
        self._shutdown_event.set()

    async def _start_caffeinate(self) -> None:
        """Start caffeinate to prevent App Nap."""
        try:
            self._caffeinate_proc = await asyncio.create_subprocess_exec(
                "/usr/bin/caffeinate",
                "-i",  # Prevent idle sleep
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            log.debug("caffeinate_started")
        except FileNotFoundError:
            log.warning("caffeinate_not_found")

    async def _stop_caffeinate(self) -> None:
        """Stop caffeinate subprocess."""
        if self._caffeinate_proc:
            self._caffeinate_proc.terminate()
            try:
                await asyncio.wait_for(self._caffeinate_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._caffeinate_proc.kill()
            self._caffeinate_proc = None
            log.debug("caffeinate_stopped")

    async def _run_loop(self) -> None:
        """Main sampling loop (placeholder for Task 23)."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.policy.current_interval,
                )
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue sampling

        await self.stop()
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add lifecycle management and signal handlers"
```

---

### Task 23: Daemon Sampling Loop

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write failing tests for sampling loop**

Add to `tests/test_daemon.py`:

```python
@pytest.mark.asyncio
async def test_daemon_collects_sample(tmp_path: Path):
    """Daemon collects and stores samples."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                # Initialize database
                from pause_monitor.storage import init_database
                init_database(config.db_path)
                daemon._conn = sqlite3.connect(config.db_path)

                # Mock powermetrics result
                from pause_monitor.collector import PowermetricsResult
                pm_result = PowermetricsResult(
                    cpu_pct=25.0,
                    cpu_freq=3000,
                    cpu_temp=65.0,
                    throttled=False,
                    gpu_pct=10.0,
                )

                sample = await daemon._collect_sample(pm_result, interval=5.0)

                assert sample is not None
                assert sample.cpu_pct == 25.0
                assert sample.stress.total >= 0


@pytest.mark.asyncio
async def test_daemon_detects_pause(tmp_path: Path):
    """Daemon detects and handles pause events."""
    config = Config()

    with patch.object(config, "db_path", tmp_path / "test.db"):
        with patch.object(config, "data_dir", tmp_path):
            with patch.object(config, "events_dir", tmp_path / "events"):
                daemon = Daemon(config)

                from pause_monitor.storage import init_database
                init_database(config.db_path)
                daemon._conn = sqlite3.connect(config.db_path)

                # Simulate a long interval (pause)
                daemon.pause_detector.expected_interval = 5.0

                with patch.object(daemon, "_handle_pause", new_callable=AsyncMock) as mock_handle:
                    await daemon._check_for_pause(actual_interval=15.0)

                    mock_handle.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_collects_sample -v`
Expected: FAIL with AttributeError

**Step 3: Implement sampling logic**

Add to `Daemon` class:

```python
    async def _collect_sample(
        self,
        pm_result,  # PowermetricsResult
        interval: float,
    ) -> Sample:
        """Collect a complete sample from all sources."""
        now = datetime.now()

        # Get system metrics
        sys_metrics = get_system_metrics()

        # Get memory pressure
        mem_pct = get_memory_pressure_fast()

        # Calculate I/O rate
        io_rate = (sys_metrics.io_read + sys_metrics.io_write)
        self.io_baseline.update(io_rate)

        # Calculate latency ratio
        latency_ratio = interval / self.policy.current_interval

        # Calculate stress score
        stress = calculate_stress(
            load_avg=sys_metrics.load_avg,
            core_count=self.core_count,
            mem_available_pct=mem_pct,
            throttled=pm_result.throttled,
            latency_ratio=latency_ratio,
            io_rate=io_rate,
            io_baseline=int(self.io_baseline.baseline_fast),
        )

        # Create sample
        sample = Sample(
            timestamp=now,
            interval=interval,
            cpu_pct=pm_result.cpu_pct,
            load_avg=sys_metrics.load_avg,
            mem_available=sys_metrics.mem_available,
            swap_used=sys_metrics.swap_used,
            io_read=sys_metrics.io_read,
            io_write=sys_metrics.io_write,
            net_sent=sys_metrics.net_sent,
            net_recv=sys_metrics.net_recv,
            cpu_temp=pm_result.cpu_temp,
            cpu_freq=pm_result.cpu_freq,
            throttled=pm_result.throttled,
            gpu_pct=pm_result.gpu_pct,
            stress=stress,
        )

        # Store sample
        if self._conn:
            insert_sample(self._conn, sample)

        # Update state
        self.state.update_sample(stress.total, now)

        # Update policy and handle state changes
        policy_result = self.policy.update(stress)
        await self._handle_policy_result(policy_result, stress)

        return sample

    async def _check_for_pause(self, actual_interval: float) -> None:
        """Check if the interval indicates a system pause."""
        recent_wake = was_recently_asleep(within_seconds=actual_interval)
        pause = self.pause_detector.check(actual_interval, recent_wake)

        if pause:
            await self._handle_pause(pause)

    async def _handle_pause(self, pause) -> None:
        """Handle a detected pause event."""
        log.warning(
            "pause_detected",
            duration=pause.duration,
            latency_ratio=pause.latency_ratio,
        )

        # Create forensics capture
        event_dir = create_event_dir(self.config.events_dir, pause.timestamp)
        capture = ForensicsCapture(event_dir)

        # Write metadata
        capture.write_metadata({
            "timestamp": pause.timestamp.isoformat(),
            "duration": pause.duration,
            "expected_interval": pause.expected,
            "latency_ratio": pause.latency_ratio,
        })

        # Run forensics capture in background
        asyncio.create_task(self._run_forensics(capture))

        # Create event record
        event = Event(
            timestamp=pause.timestamp,
            duration=pause.duration,
            stress=StressBreakdown(load=0, memory=0, thermal=0, latency=0, io=0),
            culprits=[],
            event_dir=str(event_dir),
            notes=None,
        )

        if self._conn:
            insert_event(self._conn, event)

        self.state.event_count += 1

        # Send notification
        self.notifier.pause_detected(pause.duration, event_dir)

    async def _run_forensics(self, capture: ForensicsCapture) -> None:
        """Run forensics capture and notify on completion."""
        await run_full_capture(capture)
        self.notifier.forensics_completed(capture.event_dir)

    async def _handle_policy_result(self, result, stress: StressBreakdown) -> None:
        """Handle policy state changes."""
        if result.state_changed:
            from pause_monitor.collector import SamplingState

            if self.policy.state == SamplingState.ELEVATED:
                self.state.enter_elevated()
                self.notifier.elevated_entered(stress.total)
            else:
                self.state.exit_elevated()

        # Track critical stress
        if stress.total >= self.config.sampling.critical_threshold:
            self.state.enter_critical()
            if self.state.critical_duration >= self.config.alerts.critical_duration:
                self.notifier.critical_stress(
                    stress.total,
                    self.state.critical_duration,
                )
        else:
            self.state.exit_critical()

        # Trigger preemptive snapshot if requested
        if result.should_snapshot:
            log.info("preemptive_snapshot_triggered", stress=stress.total)
            event_dir = create_event_dir(
                self.config.events_dir,
                datetime.now(),
            )
            capture = ForensicsCapture(event_dir)
            asyncio.create_task(self._run_forensics(capture))
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add src/pause_monitor/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add sampling loop and pause detection"
```

---

### Task 24: Daemon Entry Point

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `src/pause_monitor/cli.py`

**Step 1: Add run_daemon function**

Add to `src/pause_monitor/daemon.py`:

```python
async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon until shutdown.

    Args:
        config: Optional config, loads from file if not provided
    """
    if config is None:
        config = Config.load()

    # Setup logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )

    daemon = Daemon(config)

    try:
        await daemon.start()
    except Exception as e:
        log.exception("daemon_crashed", error=str(e))
        raise
    finally:
        await daemon.stop()
```

**Step 2: Wire CLI daemon command**

Replace the daemon command in `src/pause_monitor/cli.py`:

```python
@main.command()
def daemon():
    """Run the background sampler."""
    import asyncio
    from pause_monitor.daemon import run_daemon

    asyncio.run(run_daemon())
```

**Step 3: Run daemon smoke test**

Run: `uv run pause-monitor daemon --help`
Expected: Help text displays

**Step 4: Commit**

```bash
git add src/pause_monitor/daemon.py src/pause_monitor/cli.py
git commit -m "feat(daemon): add entry point and wire CLI command"
```

---

## Phase 10: TUI Dashboard

### Task 25: TUI Main Application

**Files:**
- Create: `src/pause_monitor/tui/__init__.py`
- Create: `src/pause_monitor/tui/app.py`

**Step 1: Create TUI package structure**

Create `src/pause_monitor/tui/__init__.py`:

```python
"""TUI dashboard for pause-monitor."""

from pause_monitor.tui.app import PauseMonitorApp

__all__ = ["PauseMonitorApp"]
```

**Step 2: Create main TUI application**

Create `src/pause_monitor/tui/app.py`:

```python
"""Main TUI application."""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, ProgressBar

from pause_monitor.config import Config


class StressGauge(Static):
    """Visual stress level gauge."""

    DEFAULT_CSS = """
    StressGauge {
        height: 3;
        border: solid green;
        padding: 0 1;
    }

    StressGauge.elevated {
        border: solid yellow;
    }

    StressGauge.critical {
        border: solid red;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._stress = 0

    def update_stress(self, stress: int) -> None:
        """Update the displayed stress value."""
        self._stress = stress
        self.update(f"Stress: {stress:3d}/100 {'█' * (stress // 5)}{'░' * (20 - stress // 5)}")

        # Update styling based on level
        self.remove_class("elevated", "critical")
        if stress >= 60:
            self.add_class("critical")
        elif stress >= 30:
            self.add_class("elevated")


class MetricsPanel(Static):
    """Panel showing current system metrics."""

    DEFAULT_CSS = """
    MetricsPanel {
        height: 8;
        border: solid $primary;
        padding: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._metrics = {}

    def update_metrics(self, metrics: dict) -> None:
        """Update displayed metrics."""
        self._metrics = metrics
        lines = [
            f"CPU: {metrics.get('cpu_pct', 0):.1f}%",
            f"Load: {metrics.get('load_avg', 0):.2f}",
            f"Memory: {metrics.get('mem_available', 0) / 1e9:.1f} GB free",
            f"Freq: {metrics.get('cpu_freq', 0)} MHz",
            f"Throttled: {'Yes' if metrics.get('throttled') else 'No'}",
        ]
        self.update("\n".join(lines))


class EventsTable(DataTable):
    """Table showing recent pause events."""

    DEFAULT_CSS = """
    EventsTable {
        height: 10;
    }
    """

    def on_mount(self) -> None:
        """Set up table columns."""
        self.add_column("Time", width=20)
        self.add_column("Duration", width=10)
        self.add_column("Stress", width=8)
        self.add_column("Culprits", width=30)


class PauseMonitorApp(App):
    """Main TUI application for pause-monitor."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 3;
        grid-gutter: 1;
    }

    #stress-gauge {
        column-span: 2;
    }

    #metrics {
        row-span: 1;
    }

    #breakdown {
        row-span: 1;
    }

    #events {
        column-span: 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("e", "show_events", "Events"),
        ("h", "show_history", "History"),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()

    def compose(self) -> ComposeResult:
        """Create the TUI layout."""
        yield Header()

        yield StressGauge(id="stress-gauge")
        yield MetricsPanel(id="metrics")
        yield Static("Stress Breakdown", id="breakdown")
        yield EventsTable(id="events")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize on startup."""
        self.title = "pause-monitor"
        self.sub_title = "System Health Monitor"

        # Start periodic refresh
        self.set_interval(1.0, self._refresh_data)

    def _refresh_data(self) -> None:
        """Refresh displayed data from database."""
        # This will be connected to the database in integration
        stress_gauge = self.query_one("#stress-gauge", StressGauge)
        stress_gauge.update_stress(0)

    def action_refresh(self) -> None:
        """Manual refresh."""
        self._refresh_data()

    def action_show_events(self) -> None:
        """Show events view."""
        self.notify("Events view not yet implemented")

    def action_show_history(self) -> None:
        """Show history view."""
        self.notify("History view not yet implemented")


def run_tui(config: Config | None = None) -> None:
    """Run the TUI application."""
    app = PauseMonitorApp(config)
    app.run()
```

**Step 3: Wire CLI tui command**

Replace the tui command in `src/pause_monitor/cli.py`:

```python
@main.command()
def tui():
    """Launch interactive dashboard."""
    from pause_monitor.tui import PauseMonitorApp
    from pause_monitor.config import Config

    config = Config.load()
    app = PauseMonitorApp(config)
    app.run()
```

**Step 4: Run TUI smoke test**

Run: `uv run pause-monitor tui --help`
Expected: Help text displays

**Step 5: Commit**

```bash
git add src/pause_monitor/tui/ src/pause_monitor/cli.py
git commit -m "feat(tui): add basic TUI dashboard structure"
```

---

## Phase 11: CLI Commands

### Task 26: Status Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement status command**

Replace the status command in `src/pause_monitor/cli.py`:

```python
@main.command()
def status():
    """Quick health check."""
    import sqlite3
    from datetime import datetime, timedelta
    from pause_monitor.config import Config
    from pause_monitor.storage import get_recent_samples, get_events

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    # Get latest sample
    samples = get_recent_samples(conn, limit=1)

    if not samples:
        click.echo("No samples collected yet.")
        conn.close()
        return

    latest = samples[0]
    age = (datetime.now() - latest.timestamp).total_seconds()

    # Check if daemon is running
    daemon_status = "running" if age < 30 else "stopped"

    click.echo(f"Daemon: {daemon_status}")
    click.echo(f"Last sample: {int(age)}s ago")
    click.echo(f"Stress: {latest.stress.total}/100")
    click.echo(f"  Load: {latest.stress.load}, Memory: {latest.stress.memory}, "
               f"Thermal: {latest.stress.thermal}, Latency: {latest.stress.latency}, "
               f"I/O: {latest.stress.io}")

    # Get recent events
    events = get_events(
        conn,
        start=datetime.now() - timedelta(days=1),
        limit=5,
    )

    if events:
        click.echo(f"\nRecent events (last 24h): {len(events)}")
        for event in events[:3]:
            click.echo(f"  - {event.timestamp.strftime('%H:%M:%S')}: "
                       f"{event.duration:.1f}s pause")

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement status command"
```

---

### Task 27: Events Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement events command**

Replace the events command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.argument("event_id", required=False, type=int)
@click.option("--limit", "-n", default=20, help="Number of events to show")
def events(event_id, limit):
    """List or inspect pause events."""
    import sqlite3
    from pause_monitor.config import Config
    from pause_monitor.storage import get_events, get_event_by_id

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    if event_id:
        # Show single event details
        event = get_event_by_id(conn, event_id)
        if not event:
            click.echo(f"Event {event_id} not found.")
            conn.close()
            return

        click.echo(f"Event #{event.id}")
        click.echo(f"Time: {event.timestamp}")
        click.echo(f"Duration: {event.duration:.1f}s")
        click.echo(f"Stress: {event.stress.total}/100")
        click.echo(f"  Load: {event.stress.load}")
        click.echo(f"  Memory: {event.stress.memory}")
        click.echo(f"  Thermal: {event.stress.thermal}")
        click.echo(f"  Latency: {event.stress.latency}")
        click.echo(f"  I/O: {event.stress.io}")

        if event.culprits:
            click.echo(f"Culprits: {', '.join(event.culprits)}")

        if event.event_dir:
            click.echo(f"Forensics: {event.event_dir}")

        if event.notes:
            click.echo(f"Notes: {event.notes}")
    else:
        # List events
        event_list = get_events(conn, limit=limit)

        if not event_list:
            click.echo("No events recorded.")
            conn.close()
            return

        click.echo(f"{'ID':>5}  {'Time':20}  {'Duration':>10}  {'Stress':>7}  Culprits")
        click.echo("-" * 70)

        for event in event_list:
            culprits_str = ", ".join(event.culprits[:2]) if event.culprits else "-"
            click.echo(
                f"{event.id:>5}  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S'):20}  "
                f"{event.duration:>8.1f}s  {event.stress.total:>6}/100  {culprits_str}"
            )

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement events command"
```

---

### Task 28: History Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement history command**

Replace the history command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--hours", "-h", default=24, help="Hours of history to show")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def history(hours, fmt):
    """Query historical data."""
    import sqlite3
    import json
    from datetime import datetime, timedelta
    from pause_monitor.config import Config
    from pause_monitor.storage import get_recent_samples

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found. Run 'pause-monitor daemon' first.")
        return

    conn = sqlite3.connect(config.db_path)

    # Get samples from time range
    # Note: get_recent_samples returns newest first, so we get more than needed
    # and filter by time
    cutoff = datetime.now() - timedelta(hours=hours)
    samples = get_recent_samples(conn, limit=hours * 720)  # ~1 sample/5s max
    samples = [s for s in samples if s.timestamp >= cutoff]

    if not samples:
        click.echo(f"No samples in the last {hours} hours.")
        conn.close()
        return

    if fmt == "json":
        data = [
            {
                "timestamp": s.timestamp.isoformat(),
                "stress": s.stress.total,
                "cpu_pct": s.cpu_pct,
                "load_avg": s.load_avg,
            }
            for s in samples
        ]
        click.echo(json.dumps(data, indent=2))
    elif fmt == "csv":
        click.echo("timestamp,stress,cpu_pct,load_avg")
        for s in samples:
            click.echo(f"{s.timestamp.isoformat()},{s.stress.total},{s.cpu_pct},{s.load_avg}")
    else:
        # Summary stats
        stresses = [s.stress.total for s in samples]
        click.echo(f"Samples: {len(samples)}")
        click.echo(f"Time range: {samples[-1].timestamp} to {samples[0].timestamp}")
        click.echo(f"Stress - Min: {min(stresses)}, Max: {max(stresses)}, "
                   f"Avg: {sum(stresses)/len(stresses):.1f}")

        # High stress periods
        high_stress = [s for s in samples if s.stress.total >= 30]
        if high_stress:
            click.echo(f"\nHigh stress periods: {len(high_stress)} samples")
            click.echo(f"  ({len(high_stress) / len(samples) * 100:.1f}% of time)")

    conn.close()
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement history command"
```

---

### Task 29: Config Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement config command**

Add a new config command group to `src/pause_monitor/cli.py`:

```python
@main.group()
def config():
    """Manage configuration."""
    pass


@config.command("show")
def config_show():
    """Display current configuration."""
    from pause_monitor.config import Config

    config = Config.load()

    click.echo(f"Config file: {config.config_path}")
    click.echo(f"Exists: {config.config_path.exists()}")
    click.echo()
    click.echo("[sampling]")
    click.echo(f"  normal_interval = {config.sampling.normal_interval}")
    click.echo(f"  elevated_interval = {config.sampling.elevated_interval}")
    click.echo(f"  elevation_threshold = {config.sampling.elevation_threshold}")
    click.echo(f"  critical_threshold = {config.sampling.critical_threshold}")
    click.echo()
    click.echo("[retention]")
    click.echo(f"  samples_days = {config.retention.samples_days}")
    click.echo(f"  events_days = {config.retention.events_days}")
    click.echo()
    click.echo("[alerts]")
    click.echo(f"  enabled = {config.alerts.enabled}")
    click.echo(f"  sound = {config.alerts.sound}")
    click.echo()
    click.echo(f"learning_mode = {config.learning_mode}")


@config.command("edit")
def config_edit():
    """Open config file in editor."""
    import subprocess
    import os
    from pause_monitor.config import Config

    config = Config.load()

    # Create config if it doesn't exist
    if not config.config_path.exists():
        config.save()
        click.echo(f"Created default config at {config.config_path}")

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(config.config_path)])


@config.command("reset")
@click.confirmation_option(prompt="Reset config to defaults?")
def config_reset():
    """Reset configuration to defaults."""
    from pause_monitor.config import Config

    config = Config()
    config.save()
    click.echo(f"Config reset to defaults at {config.config_path}")
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement config command group"
```

---

### Task 30: Prune Command

**Files:**
- Modify: `src/pause_monitor/cli.py`
- Modify: `src/pause_monitor/storage.py`

**Step 1: Add prune function to storage**

Add to `src/pause_monitor/storage.py`:

```python
def prune_old_data(
    conn: sqlite3.Connection,
    samples_days: int = 30,
    events_days: int = 90,
) -> tuple[int, int]:
    """Delete old samples and events.

    Args:
        conn: Database connection
        samples_days: Delete samples older than this
        events_days: Delete events older than this

    Returns:
        Tuple of (samples_deleted, events_deleted)
    """
    cutoff_samples = time.time() - (samples_days * 86400)
    cutoff_events = time.time() - (events_days * 86400)

    # Delete old process samples first (foreign key)
    conn.execute(
        """
        DELETE FROM process_samples
        WHERE sample_id IN (SELECT id FROM samples WHERE timestamp < ?)
        """,
        (cutoff_samples,),
    )

    # Delete old samples
    cursor = conn.execute(
        "DELETE FROM samples WHERE timestamp < ?",
        (cutoff_samples,),
    )
    samples_deleted = cursor.rowcount

    # Delete old events
    cursor = conn.execute(
        "DELETE FROM events WHERE timestamp < ?",
        (cutoff_events,),
    )
    events_deleted = cursor.rowcount

    conn.commit()

    log.info(
        "prune_complete",
        samples_deleted=samples_deleted,
        events_deleted=events_deleted,
    )

    return samples_deleted, events_deleted
```

**Step 2: Add prune command to CLI**

Add to `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--samples-days", default=None, type=int, help="Override sample retention days")
@click.option("--events-days", default=None, type=int, help="Override event retention days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
def prune(samples_days, events_days, dry_run):
    """Delete old data per retention policy."""
    import sqlite3
    from pause_monitor.config import Config
    from pause_monitor.storage import prune_old_data

    config = Config.load()

    if not config.db_path.exists():
        click.echo("Database not found.")
        return

    samples_days = samples_days or config.retention.samples_days
    events_days = events_days or config.retention.events_days

    if dry_run:
        click.echo(f"Would prune samples older than {samples_days} days")
        click.echo(f"Would prune events older than {events_days} days")
        return

    conn = sqlite3.connect(config.db_path)
    samples_deleted, events_deleted = prune_old_data(
        conn,
        samples_days=samples_days,
        events_days=events_days,
    )
    conn.close()

    click.echo(f"Deleted {samples_deleted} samples, {events_deleted} events")
```

**Step 3: Commit**

```bash
git add src/pause_monitor/storage.py src/pause_monitor/cli.py
git commit -m "feat(cli): implement prune command with retention policy"
```

---

## Phase 12: Install/Uninstall

### Task 31: Install Command (launchd)

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement install command with modern launchctl syntax**

Replace the install command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--user", is_flag=True, default=True, help="Install for current user (default)")
@click.option("--system", "system_wide", is_flag=True, help="Install system-wide (requires root)")
def install(user, system_wide):
    """Set up launchd service."""
    import subprocess
    import sys
    from pathlib import Path

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
        label = "com.pause-monitor.daemon"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"
        label = "com.pause-monitor.daemon"

    plist_path = plist_dir / f"{label}.plist"

    # Get Python path
    python_path = sys.executable

    # Create plist content
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>pause_monitor.cli</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.local/share/pause-monitor/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.local/share/pause-monitor/daemon.log</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
"""

    # Create directory if needed
    plist_dir.mkdir(parents=True, exist_ok=True)

    # Write plist
    plist_path.write_text(plist_content)
    click.echo(f"Created {plist_path}")

    # Bootstrap the service (modern launchctl syntax)
    try:
        subprocess.run(
            ["launchctl", "bootstrap", service_target, str(plist_path)],
            check=True,
            capture_output=True,
        )
        click.echo(f"Service installed and started")
    except subprocess.CalledProcessError as e:
        # May already be loaded
        if b"already loaded" in e.stderr or b"service already loaded" in e.stderr.lower():
            click.echo("Service was already installed")
        else:
            click.echo(f"Warning: Could not start service: {e.stderr.decode()}")

    click.echo(f"\nTo check status: launchctl print {service_target}/{label}")
    click.echo(f"To view logs: tail -f ~/.local/share/pause-monitor/daemon.log")
```

**Step 2: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement install with modern launchctl bootstrap"
```

---

### Task 32: Uninstall Command

**Files:**
- Modify: `src/pause_monitor/cli.py`

**Step 1: Implement uninstall command**

Replace the uninstall command in `src/pause_monitor/cli.py`:

```python
@main.command()
@click.option("--user", is_flag=True, default=True, help="Uninstall user service (default)")
@click.option("--system", "system_wide", is_flag=True, help="Uninstall system service")
@click.option("--keep-data", is_flag=True, help="Keep database and config files")
def uninstall(user, system_wide, keep_data):
    """Remove launchd service."""
    import subprocess
    import shutil
    from pathlib import Path

    # Determine paths
    if system_wide:
        plist_dir = Path("/Library/LaunchDaemons")
        service_target = "system"
        label = "com.pause-monitor.daemon"
    else:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        service_target = f"gui/{os.getuid()}"
        label = "com.pause-monitor.daemon"

    plist_path = plist_dir / f"{label}.plist"

    # Bootout the service (modern launchctl syntax)
    if plist_path.exists():
        try:
            subprocess.run(
                ["launchctl", "bootout", f"{service_target}/{label}"],
                check=True,
                capture_output=True,
            )
            click.echo("Service stopped")
        except subprocess.CalledProcessError as e:
            if b"No such process" not in e.stderr:
                click.echo(f"Warning: Could not stop service: {e.stderr.decode()}")

        # Remove plist
        plist_path.unlink()
        click.echo(f"Removed {plist_path}")
    else:
        click.echo("Service was not installed")

    # Optionally remove data
    if not keep_data:
        from pause_monitor.config import Config
        config = Config()

        if config.data_dir.exists():
            if click.confirm(f"Delete data directory {config.data_dir}?"):
                shutil.rmtree(config.data_dir)
                click.echo(f"Removed {config.data_dir}")

        if config.config_dir.exists():
            if click.confirm(f"Delete config directory {config.config_dir}?"):
                shutil.rmtree(config.config_dir)
                click.echo(f"Removed {config.config_dir}")

    click.echo("Uninstall complete")
```

**Step 2: Add import for os module at top of cli.py**

Add to imports in `src/pause_monitor/cli.py`:

```python
import os
```

**Step 3: Commit**

```bash
git add src/pause_monitor/cli.py
git commit -m "feat(cli): implement uninstall with modern launchctl bootout"
```

---

## Phase 13: Final Integration

### Task 33: PID File Management

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Add PID file handling**

Add to `Daemon` class in `src/pause_monitor/daemon.py`:

```python
    def _write_pid_file(self) -> None:
        """Write PID file."""
        self.config.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.pid_path.write_text(str(os.getpid()))
        log.debug("pid_file_written", path=str(self.config.pid_path))

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        if self.config.pid_path.exists():
            self.config.pid_path.unlink()
            log.debug("pid_file_removed")

    def _check_already_running(self) -> bool:
        """Check if daemon is already running."""
        if not self.config.pid_path.exists():
            return False

        try:
            pid = int(self.config.pid_path.read_text().strip())
            # Check if process exists
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file exists but process doesn't - stale file
            self._remove_pid_file()
            return False
```

**Step 2: Wire PID file into start/stop**

Add to `start()` method after signal handlers:

```python
        # Check for existing instance
        if self._check_already_running():
            log.error("daemon_already_running")
            raise RuntimeError("Daemon is already running")

        self._write_pid_file()
```

Add to `stop()` method:

```python
        self._remove_pid_file()
```

**Step 3: Add os import at top of daemon.py**

```python
import os
```

**Step 4: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "feat(daemon): add PID file management"
```

---

### Task 34: Auto-Pruning Integration

**Files:**
- Modify: `src/pause_monitor/daemon.py`

**Step 1: Add auto-prune to daemon**

Add method to `Daemon` class:

```python
    async def _auto_prune(self) -> None:
        """Run automatic data pruning daily."""
        from pause_monitor.storage import prune_old_data

        while not self._shutdown_event.is_set():
            try:
                # Wait for 24 hours or shutdown
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=86400,  # 24 hours
                )
                break
            except asyncio.TimeoutError:
                # Run prune
                if self._conn:
                    prune_old_data(
                        self._conn,
                        samples_days=self.config.retention.samples_days,
                        events_days=self.config.retention.events_days,
                    )
```

**Step 2: Start auto-prune task in start()**

Add after `self.state.running = True`:

```python
        # Start auto-prune task
        asyncio.create_task(self._auto_prune())
```

**Step 3: Commit**

```bash
git add src/pause_monitor/daemon.py
git commit -m "feat(daemon): add automatic data pruning"
```

---

### Task 35: Full Test Suite Run

**Files:**
- None (verification only)

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors

**Step 3: Run formatter check**

Run: `uv run ruff format --check .`
Expected: All files formatted

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: address test and lint issues"
```

---

### Task 36: Documentation Update

**Files:**
- Verify: `CLAUDE.md`

**Step 1: Verify CLAUDE.md is accurate**

Review that the CLAUDE.md in the project root accurately describes all implemented commands and architecture.

**Step 2: Final commit**

```bash
git add -A
git commit -m "docs: finalize documentation"
```

---

## Summary

This implementation plan covers 36 tasks across 13 phases:

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-3 | Core Infrastructure (dependencies, config) |
| 2 | 4-7 | Stress Detection (StressBreakdown FIRST) |
| 3 | 8 | Test Infrastructure (conftest.py) |
| 4 | 9 | Storage Layer (schema) |
| 5 | 10-15 | Metrics Collection (Sample/Event, collector, policy) |
| 6 | 16-17 | Sleep/Wake and Pause Detection |
| 7 | 18-19 | Forensics Capture (spindump, tailspin, logs) |
| 8 | 20 | Notifications |
| 9 | 21-24 | Daemon Core (state, lifecycle, sampling, entry) |
| 10 | 25 | TUI Dashboard |
| 11 | 26-30 | CLI Commands (status, events, history, config, prune) |
| 12 | 31-32 | Install/Uninstall (modern launchctl) |
| 13 | 33-36 | Final Integration (PID file, auto-prune, tests, docs) |

**Key fixes from validation:**
- StressBreakdown defined in Phase 2 BEFORE storage operations in Phase 5
- Config uses `Config.load()` class method consistently
- Sample dataclass fields unified across all modules
- conftest.py provides shared test fixtures
- Streaming powermetrics subprocess (not exec-per-sample)
- tailspin integration for kernel traces
- Modern launchctl bootstrap/bootout syntax
- caffeinate for App Nap prevention
- Signal handlers for graceful shutdown
- Forensics and notifications wired to daemon
