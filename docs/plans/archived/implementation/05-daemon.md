# Part 5: Daemon

> **Navigation:** [Index](./index.md) | [Prev: Response](./04-response.md) | **Current** | [Next: Interface](./06-interface.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 9 (Daemon Core)
**Tasks:** 21-24
**Dependencies:** Parts 1-4 (all previous components)

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
        """Main sampling loop - streams powermetrics and processes samples."""
        import time

        # Start powermetrics stream
        await self._powermetrics.start()

        last_sample_time = time.monotonic()

        try:
            async for pm_result in self._powermetrics.read_samples():
                if self._shutdown_event.is_set():
                    break

                # Calculate actual interval (for pause detection)
                now = time.monotonic()
                actual_interval = now - last_sample_time
                last_sample_time = now

                # Check for pause first
                await self._check_for_pause(actual_interval)

                # Collect and store sample
                await self._collect_sample(pm_result, actual_interval)

                # Update pause detector's expected interval based on current policy
                self.pause_detector.expected_interval = self.policy.current_interval

        except asyncio.CancelledError:
            log.info("sampling_loop_cancelled")
        except Exception as e:
            log.exception("sampling_loop_error", error=str(e))
            raise

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


---

> **Next:** [Part 6: Interface](./06-interface.md)
