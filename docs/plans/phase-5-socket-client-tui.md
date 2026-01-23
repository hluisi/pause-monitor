# Phase 5: Update TUI to Use Socket

Part of [pause-monitor Redesign Implementation Plan](2026-01-21-pause-monitor-implementation.md)

---

## CRITICAL: Read This First (For AI Agents)

> **This is a PERSONAL PROJECT — one developer + AI assistants. NO external users. NO backwards compatibility.**

| Principle | What This Means | Anti-Pattern to AVOID |
|-----------|-----------------|----------------------|
| **Delete, don't deprecate** | If code is replaced, DELETE the old code | `@deprecated`, "kept for compatibility" |
| **No dead code** | Superseded code = DELETE it immediately | "might need later", commented-out code |
| **No stubs** | Implement it or don't include it | `return (0, 0)`, `pass`, `NotImplementedError` |
| **No migrations** | Schema changes? Delete the DB file, recreate fresh | `migrate_add_*()`, `ALTER TABLE` |
| **Breaking changes are FREE** | Change anything. No versioning needed. | `_v2` suffixes, compatibility shims |

**Implementation rule:** If old code conflicts with this plan → DELETE IT. If you see migration code → DELETE IT AND USE SCHEMA_VERSION CHECK INSTEAD.

**Database philosophy:** When schema changes, increment `SCHEMA_VERSION`. At startup, if version doesn't match, delete `data.db` and recreate. No migrations. Ever.

---

> **Sub-skill:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable real-time 10Hz TUI dashboard with complete 8-factor stress monitoring (including pageins), tier-appropriate forensics, and Unix socket streaming.

**Architecture:** Single 100ms loop driven by powermetrics. Ring buffer is the source of truth. Socket streams to TUI. SQLite stores only tier events (elevated bookmarks, pause forensics).

**Tech Stack:** Python 3.14, asyncio, Unix domain sockets, Textual TUI, SQLite (history only)

---

## Summary

2 tasks adding Unix socket client and TUI integration:
- Task 5.1: Create SocketClient class (simple, stateless)
- Task 5.2: Update TUI to connect via socket

---

> **⚠️ SIMPLIFIED: No Auto-Reconnect**
>
> Per the "Design Simplifications" section above, the socket client is **simple and stateless**. It connects or throws. The TUI decides what to do on disconnect.
>
> Key changes from original task specs:
> - Remove `on_disconnect` / `on_reconnect` callbacks
> - Remove `reconnect_interval` parameter
> - Remove `_reconnect()` and `_read_loop()` methods
> - `connect()` raises `FileNotFoundError` if daemon not running
> - `read_message()` raises `ConnectionError` on disconnect
> - TUI handles reconnection logic in its own event loop

---

## Task 5.1: Create SocketClient Class (SIMPLIFIED)

**Files:**
- Create: `src/pause_monitor/socket_client.py`
- Create: `tests/test_socket_client.py`

**Step 1: Write the failing test**

```python
# tests/test_socket_client.py

import asyncio
import json
import pytest
from pathlib import Path

from pause_monitor.socket_client import SocketClient


@pytest.mark.asyncio
async def test_socket_client_receives_data(tmp_path):
    """SocketClient should receive and parse messages."""
    socket_path = tmp_path / "test.sock"

    # Start mock server
    async def handle_client(reader, writer):
        msg = {"samples": [], "tier": 2, "current_stress": {"load": 5}}
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server = await asyncio.start_unix_server(handle_client, path=str(socket_path))

    try:
        client = SocketClient(socket_path=socket_path)
        await client.connect()

        # Read one message
        data = await client.read_message()
        assert data["tier"] == 2

        await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_socket_client_raises_on_connection_failure(tmp_path):
    """SocketClient should raise FileNotFoundError if daemon not running."""
    socket_path = tmp_path / "nonexistent.sock"

    client = SocketClient(socket_path=socket_path)

    with pytest.raises(FileNotFoundError):
        await client.connect()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_socket_client.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/socket_client.py

"""Unix socket client for receiving ring buffer data from daemon."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class SocketClient:
    """Unix domain socket client for real-time ring buffer data.

    Simple and stateless: connects or throws. TUI handles reconnection.
    """

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.on_data: Callable[[dict[str, Any]], None] | None = None

    @property
    def connected(self) -> bool:
        """Whether client is connected."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect to the daemon socket.

        Raises:
            FileNotFoundError: If socket doesn't exist (daemon not running)
        """
        if not self.socket_path.exists():
            raise FileNotFoundError(f"Socket not found: {self.socket_path}")

        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self.socket_path)
        )
        log.info("socket_client_connected", path=str(self.socket_path))

    async def disconnect(self) -> None:
        """Disconnect from the daemon socket."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        log.info("socket_client_disconnected")

    async def read_message(self) -> dict[str, Any]:
        """Read next message from socket.

        Returns:
            Parsed JSON message from daemon

        Raises:
            ConnectionError: If connection is lost
            json.JSONDecodeError: If message is invalid JSON
        """
        if not self._reader:
            raise ConnectionError("Not connected")

        line = await self._reader.readline()
        if not line:
            raise ConnectionError("Connection closed by server")

        return json.loads(line.decode())
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_socket_client.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/socket_client.py tests/test_socket_client.py
git commit -m "feat: add Unix socket client for TUI"
```

---

## Task 5.2: Update TUI to Connect via Socket

**Files:**
- Modify: `src/pause_monitor/tui/app.py`
- Create: `tests/test_tui_connection.py`

**Step 1: Write the failing test for fallback logic**

```python
# tests/test_tui_connection.py

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from pause_monitor.config import Config


@pytest.mark.asyncio
async def test_tui_uses_socket_when_available(tmp_path):
    """TUI should connect via socket when daemon is running."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    config._data_dir = tmp_path

    # Create a fake socket file to simulate daemon running
    config.socket_path.touch()

    app = PauseMonitorApp(config)

    with patch.object(app, '_socket_client') as mock_client:
        mock_client.connect = AsyncMock()
        # Simulate successful socket connection
        with patch('pause_monitor.tui.app.SocketClient') as MockSocketClient:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            MockSocketClient.return_value = mock_instance

            await app.on_mount()

            assert app._use_socket is True
            mock_instance.connect.assert_called_once()


@pytest.mark.asyncio
async def test_tui_shows_waiting_state_when_no_daemon(tmp_path):
    """TUI should show waiting state when daemon not running."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    config._data_dir = tmp_path

    # No socket file (daemon not running)
    assert not config.socket_path.exists()

    app = PauseMonitorApp(config)

    with patch.object(app, 'notify'):  # Don't actually notify
        with patch('asyncio.create_task'):  # Don't start background task
            await app.on_mount()

    assert "waiting" in app.sub_title.lower()


def test_tui_updates_subtitle_on_disconnect():
    """TUI should show error state when daemon connection is lost."""
    from pause_monitor.tui.app import PauseMonitorApp

    config = Config()
    app = PauseMonitorApp(config)
    app.sub_title = "System Health Monitor (live)"

    # Simulate connection error
    app._set_disconnected()

    assert "not running" in app.sub_title.lower() or "error" in app.sub_title.lower()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_connection.py -v`
Expected: FAIL (TUI doesn't have socket logic yet)

**Step 3: Add socket client import and attribute**

Add imports at top of `src/pause_monitor/tui/app.py`:
```python
from typing import Any
from pause_monitor.socket_client import SocketClient
```

Add to `PauseMonitorApp.__init__`:
```python
self._socket_client: SocketClient | None = None
```

**Step 4: Update on_mount to connect to daemon**

Replace or update the `on_mount` method:
```python
async def on_mount(self) -> None:
    """Initialize on startup."""
    self.title = "pause-monitor"

    # Create socket client
    self._socket_client = SocketClient(socket_path=self.config.socket_path)

    # Try initial connection
    try:
        await self._socket_client.connect()
        self.sub_title = "System Health Monitor (live)"
        log.info("tui_connected_via_socket")
        # Start reading messages
        asyncio.create_task(self._read_socket_loop())
    except FileNotFoundError:
        # Daemon not running - show error state
        self._set_disconnected()
        self.notify(
            "Daemon not running. Start with: sudo pause-monitor daemon",
            severity="warning",
        )

async def _read_socket_loop(self) -> None:
    """Read messages from socket and update UI."""
    try:
        while True:
            data = await self._socket_client.read_message()
            self._handle_socket_data(data)
    except ConnectionError:
        self._set_disconnected()
        log.warning("tui_daemon_disconnected")

def _set_disconnected(self) -> None:
    """Update UI to show disconnected state."""
    self.sub_title = "System Health Monitor (daemon not running)"
```

**Step 5: Add socket data handler**

```python
def _handle_socket_data(self, data: dict[str, Any]) -> None:
    """Handle real-time data from daemon socket."""
    current_stress = data.get("current_stress")
    tier = data.get("tier", 1)

    if not current_stress:
        return

    # Calculate total stress
    total = sum(current_stress.values())

    # Update stress gauge
    try:
        stress_gauge = self.query_one("#stress-gauge", StressGauge)
        stress_gauge.update_stress(total)
    except Exception:
        pass

    # Update stress breakdown
    try:
        breakdown = self.query_one("#breakdown", Static)
        breakdown.update(
            f"Load: {current_stress.get('load', 0):3d}  "
            f"Memory: {current_stress.get('memory', 0):3d}  "
            f"GPU: {current_stress.get('gpu', 0):3d}\n"
            f"Thermal: {current_stress.get('thermal', 0):3d}  "
            f"Latency: {current_stress.get('latency', 0):3d}  "
            f"Wakeups: {current_stress.get('wakeups', 0):3d}\n"
            f"I/O: {current_stress.get('io', 0):3d}  "
            f"Tier: {tier}"
        )
    except Exception:
        pass
```

**Step 6: Update on_unmount**

```python
async def on_unmount(self) -> None:
    """Clean up on shutdown."""
    if self._socket_client:
        await self._socket_client.disconnect()
```

**Step 7: Run tests to verify connection logic**

Run: `uv run pytest tests/test_tui_connection.py -v`
Expected: PASS

**Step 8: Manual testing**

```bash
# Terminal 1: Start daemon (needs sudo for powermetrics)
sudo uv run pause-monitor daemon

# Terminal 2: Start TUI
uv run pause-monitor tui
# Should show "(live)" in subtitle
```

**Step 9: Commit**

```bash
git add src/pause_monitor/tui/app.py tests/test_tui_connection.py
git commit -m "feat(tui): connect via socket for real-time data"
```
