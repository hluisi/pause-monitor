# Part 4: Response

> **Navigation:** [Index](./index.md) | [Prev: Collection](./03-collection.md) | **Current** | [Next: Daemon](./05-daemon.md)
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Phases covered:** 7-8 (Forensics Capture + Notifications)
**Tasks:** 18-20
**Dependencies:** Part 1 (config.py)

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


---

> **Next:** [Part 5: Daemon](./05-daemon.md)
