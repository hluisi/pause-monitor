# Phase 4: Add Socket Server

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

2 tasks adding Unix domain socket server for real-time TUI streaming:
- Task 4.1: Create SocketServer class (push-based design)
- Task 4.2: Integrate SocketServer into Daemon

---

> **⚠️ SIMPLIFIED: Push-Based Design**
>
> Per the "Design Simplifications" section above, the socket server uses **push-based** streaming instead of poll-based. The main loop pushes directly to connected clients—no separate broadcast loop needed.
>
> Key changes from original task specs:
> - Remove `_broadcast_loop()` method
> - Remove `broadcast_interval_ms` parameter
> - Add `broadcast(stress, tier)` method called from main loop
> - `_handle_client` just manages connection lifecycle, doesn't send data

---

## Task 4.1: Create SocketServer Class (SIMPLIFIED)

**Files:**
- Create: `src/pause_monitor/socket_server.py`
- Create: `tests/test_socket_server.py`

**Step 1: Write the failing test**

```python
# tests/test_socket_server.py

import asyncio
import json
import pytest
from pathlib import Path

from pause_monitor.socket_server import SocketServer
from pause_monitor.ringbuffer import RingBuffer
from pause_monitor.stress import StressBreakdown


@pytest.mark.asyncio
async def test_socket_server_starts_and_stops(tmp_path):
    """SocketServer should start listening and stop cleanly."""
    socket_path = tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)

    await server.start()
    assert socket_path.exists()

    await server.stop()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_socket_server_streams_to_client(tmp_path):
    """SocketServer should stream ring buffer data to clients."""
    socket_path = tmp_path / "test.sock"
    buffer = RingBuffer(max_samples=10)

    # Add samples (Phase 1: push requires metrics)
    metrics = PowermetricsResult(
        elapsed_ns=100_000_000,
        throttled=False,
        cpu_power=5.0,
        gpu_pct=10.0,
        gpu_power=1.0,
        io_read_per_s=1000.0,
        io_write_per_s=500.0,
        wakeups_per_s=100.0,
        pageins_per_s=0.0,
        top_cpu_processes=[],
        top_pagein_processes=[],
    )
    stress = StressBreakdown(load=10, memory=5, thermal=0, latency=2, io=0, gpu=15, wakeups=3, pageins=0)
    buffer.push(metrics, stress, tier=1)

    server = SocketServer(socket_path=socket_path, ring_buffer=buffer)
    await server.start()

    try:
        # Connect as client
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        # Read first message
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        message = json.loads(data.decode())

        assert "samples" in message
        assert "tier" in message
        assert len(message["samples"]) == 1
        assert message["samples"][0]["stress"]["load"] == 10
        assert message["samples"][0]["stress"]["gpu"] == 15

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# src/pause_monitor/socket_server.py

"""Unix socket server for streaming ring buffer data to TUI."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pause_monitor.ringbuffer import RingBuffer

log = logging.getLogger(__name__)


class StressDict(TypedDict):
    """Stress breakdown as dict for JSON serialization."""
    load: int
    memory: int
    thermal: int
    latency: int
    io: int
    gpu: int
    wakeups: int


class SampleDict(TypedDict):
    """Ring buffer sample as dict for JSON serialization."""
    timestamp: float
    stress: StressDict
    tier: int


class SocketMessage(TypedDict):
    """Message sent from daemon to TUI via socket."""
    samples: list[SampleDict]
    tier: int
    current_stress: StressDict | None
    sample_count: int


class SocketServer:
    """Unix domain socket server for real-time streaming to TUI.

    PUSH-BASED DESIGN (per Design Simplifications):
    - Main loop calls broadcast() after each powermetrics sample
    - No internal polling loop - data flows directly from daemon
    - Protocol: newline-delimited JSON messages
    """

    def __init__(
        self,
        socket_path: Path,
        ring_buffer: RingBuffer,
    ):
        self.socket_path = socket_path
        self.ring_buffer = ring_buffer
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._running = False
        # REMOVED: broadcast_interval_ms, _broadcast_task (push-based, not poll-based)

    @property
    def has_clients(self) -> bool:
        """Check if any clients are connected (for main loop optimization)."""
        return len(self._clients) > 0

    async def start(self) -> None:
        """Start the socket server."""
        # Remove stale socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self._running = True
        # REMOVED: self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        log.info("socket_server_started", path=str(self.socket_path))

    async def stop(self) -> None:
        """Stop the socket server."""
        self._running = False

        # REMOVED: _broadcast_task cancellation (no longer exists)

        # Close all client connections
        for writer in list(self._clients):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        log.info("socket_server_stopped")

    async def broadcast(
        self,
        metrics: PowermetricsResult,
        stress: StressBreakdown,
        tier: int,
    ) -> None:
        """Push current sample to all connected clients.
        
        Called from main loop after each powermetrics sample.
        This is the push-based approach - no internal polling.
        
        Args:
            metrics: Raw powermetrics data (Phase 1 format)
            stress: Computed stress breakdown
            tier: Current tier (1, 2, or 3)
        """
        if not self._clients:
            return
        
        # Build message with current sample data
        message: SocketMessage = {
            "timestamp": datetime.now().isoformat(),
            "tier": tier,
            "stress": StressDict(**asdict(stress)),
            "metrics": {
                "io_read_per_s": metrics.io_read_per_s,
                "io_write_per_s": metrics.io_write_per_s,
                "wakeups_per_s": metrics.wakeups_per_s,
                "gpu_pct": metrics.gpu_pct,
                "cpu_power": metrics.cpu_power,
                "gpu_power": metrics.gpu_power,
                "throttled": metrics.throttled,
            },
            "sample_count": len(self.ring_buffer.samples),
        }
        
        data = json.dumps(message).encode() + b"\n"
        
        # Send to all clients, removing any that fail
        for writer in list(self._clients):
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                self._clients.discard(writer)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection."""
        self._clients.add(writer)
        log.info("socket_client_connected", count=len(self._clients))

        try:
            # Send initial state from ring buffer
            await self._send_initial_state(writer)

            # Keep connection alive until client disconnects
            # Client just needs to stay connected; data comes via broadcast()
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=1.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    continue
                except ConnectionError:
                    break
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("socket_client_disconnected", count=len(self._clients))

    # REMOVED: async def _broadcast_loop(self) - push-based, not poll-based

    async def _send_initial_state(self, writer: asyncio.StreamWriter) -> None:
        """Send current ring buffer state to a newly connected client."""
        samples = self.ring_buffer.samples
        latest = samples[-1] if samples else None

        message: SocketMessage = {
            "type": "initial_state",
            "samples": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "stress": StressDict(**asdict(s.stress)),
                    "tier": s.tier,
                    # Include raw metrics from Phase 1 RingSample
                    "metrics": {
                        "io_read_per_s": s.metrics.io_read_per_s,
                        "io_write_per_s": s.metrics.io_write_per_s,
                        "wakeups_per_s": s.metrics.wakeups_per_s,
                        "gpu_pct": s.metrics.gpu_pct,
                        "throttled": s.metrics.throttled,
                    },
                }
                for s in samples[-30:]  # Last 3 seconds
            ],
            "tier": latest.tier if latest else 1,
            "current_stress": StressDict(**asdict(latest.stress)) if latest else None,
            "sample_count": len(samples),
        }

        data = json.dumps(message).encode() + b"\n"
        writer.write(data)
        await writer.drain()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pause_monitor/socket_server.py tests/test_socket_server.py
git commit -m "feat: add Unix socket server for TUI streaming"
```

---

## Task 4.2: Integrate SocketServer into Daemon

**Files:**
- Modify: `src/pause_monitor/daemon.py`
- Modify: `src/pause_monitor/config.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# tests/test_daemon.py - add to existing file

@pytest.mark.asyncio
async def test_daemon_socket_available_after_start(tmp_path, monkeypatch):
    """Daemon should have socket server listening after start."""
    config = Config()
    config._data_dir = tmp_path

    daemon = Daemon(config)

    # Mock _main_loop to exit immediately (we just want to test socket wiring)
    async def mock_main_loop():
        pass
    monkeypatch.setattr(daemon, "_main_loop", mock_main_loop)

    # Start daemon (will return after mock_main_loop completes)
    await daemon.start()

    # Socket file should exist and server should be listening
    assert config.socket_path.exists(), "Socket file should exist after daemon start"

    # Verify we can connect
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    writer.close()
    await writer.wait_closed()

    await daemon.stop()
    assert not config.socket_path.exists(), "Socket file should be cleaned up after stop"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_daemon_socket_available_after_start -v`
Expected: FAIL (no socket server integration yet)

**Step 3: Add socket_path to Config**

```python
# src/pause_monitor/config.py - add to Config class

@property
def socket_path(self) -> Path:
    """Path to daemon Unix socket."""
    return self.data_dir / "daemon.sock"
```

**Step 4: Add SocketServer to Daemon**

Add import at top of `src/pause_monitor/daemon.py`:
```python
from pause_monitor.socket_server import SocketServer
```

Add to `Daemon.__init__`:
```python
self._socket_server: SocketServer | None = None
```

Add to `Daemon.start()` (after caffeinate, before `self.state.running = True`):
```python
# Start socket server for TUI (push-based - no broadcast_interval_ms)
self._socket_server = SocketServer(
    socket_path=self.config.socket_path,
    ring_buffer=self.ring_buffer,
)
await self._socket_server.start()
```

Update `Daemon.stop()`:
```python
if self._socket_server:
    await self._socket_server.stop()
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_daemon.py::test_daemon_socket_available_after_start -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pause_monitor/daemon.py src/pause_monitor/config.py
git commit -m "feat(daemon): integrate socket server for TUI"
```
