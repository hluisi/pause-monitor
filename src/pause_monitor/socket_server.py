# src/pause_monitor/socket_server.py
"""Unix socket server for streaming ring buffer data to TUI.

PUSH-BASED DESIGN (per Design Simplifications):
- Main loop calls broadcast() after each powermetrics sample
- No internal polling loop - data flows directly from daemon
- Protocol: newline-delimited JSON messages
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pause_monitor.collector import PowermetricsResult
    from pause_monitor.ringbuffer import RingBuffer
    from pause_monitor.stress import StressBreakdown

log = logging.getLogger(__name__)


class SocketServer:
    """Unix domain socket server for real-time streaming to TUI.

    PUSH-BASED DESIGN (per Design Simplifications):
    - Main loop calls broadcast() after each powermetrics sample
    - No internal polling loop - data flows directly from daemon
    - Protocol: newline-delimited JSON messages

    Message Types:
    - initial_state: Sent on client connect with recent buffer samples
    - sample: Sent via broadcast() with current metrics/stress/tier
    """

    def __init__(
        self,
        socket_path: Path,
        ring_buffer: RingBuffer,
    ) -> None:
        self.socket_path = socket_path
        self.ring_buffer = ring_buffer
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._running = False

    @property
    def has_clients(self) -> bool:
        """Check if any clients are connected (for main loop optimization)."""
        return len(self._clients) > 0

    async def start(self) -> None:
        """Start the socket server."""
        import os
        import stat

        # Remove stale socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )

        # Make socket accessible to non-root users (daemon runs as root, TUI as user)
        os.chmod(self.socket_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

        self._running = True
        log.info("socket_server_started path=%s", self.socket_path)

    async def stop(self) -> None:
        """Stop the socket server."""
        self._running = False

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
        *,
        load_avg: float = 0.0,
        mem_pressure: int = 0,
    ) -> None:
        """Push current sample to all connected clients.

        Called from main loop after each powermetrics sample.
        This is the push-based approach - no internal polling.

        Args:
            metrics: Raw powermetrics data (Phase 1 format)
            stress: Computed stress breakdown
            tier: Current tier (1, 2, or 3)
            load_avg: System load average (1 minute)
            mem_pressure: Memory pressure percentage (0-100, higher = more free)
        """
        if not self._clients:
            return

        # Build message with current sample data
        message = {
            "type": "sample",
            "timestamp": datetime.now().isoformat(),
            "tier": tier,
            "stress": asdict(stress),
            "metrics": {
                "elapsed_ns": metrics.elapsed_ns,
                "throttled": metrics.throttled,
                "cpu_power": metrics.cpu_power,
                "gpu_pct": metrics.gpu_pct,
                "gpu_power": metrics.gpu_power,
                "io_read_per_s": metrics.io_read_per_s,
                "io_write_per_s": metrics.io_write_per_s,
                "wakeups_per_s": metrics.wakeups_per_s,
                "pageins_per_s": metrics.pageins_per_s,
                "top_cpu_processes": metrics.top_cpu_processes,
                "top_pagein_processes": metrics.top_pagein_processes,
                "load_avg": load_avg,
                "mem_pressure": mem_pressure,
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
        log.info("socket_client_connected count=%d", len(self._clients))

        try:
            # Send initial state from ring buffer
            try:
                await self._send_initial_state(writer)
            except Exception:
                log.debug("socket_initial_state_failed")
                return  # Client disconnected, cleanup happens in finally

            # Keep connection alive until client disconnects
            # Client just needs to stay connected; data comes via broadcast()
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=1.0)
                    if not data:
                        break
                except TimeoutError:
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
            log.info("socket_client_disconnected count=%d", len(self._clients))

    async def _send_initial_state(self, writer: asyncio.StreamWriter) -> None:
        """Send current ring buffer state to a newly connected client."""
        samples = self.ring_buffer.samples
        latest = samples[-1] if samples else None

        message = {
            "type": "initial_state",
            "samples": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "stress": asdict(s.stress),
                    "tier": s.tier,
                    # Include raw metrics from Phase 1 RingSample
                    "metrics": {
                        "elapsed_ns": s.metrics.elapsed_ns,
                        "throttled": s.metrics.throttled,
                        "cpu_power": s.metrics.cpu_power,
                        "gpu_pct": s.metrics.gpu_pct,
                        "gpu_power": s.metrics.gpu_power,
                        "io_read_per_s": s.metrics.io_read_per_s,
                        "io_write_per_s": s.metrics.io_write_per_s,
                        "wakeups_per_s": s.metrics.wakeups_per_s,
                        "pageins_per_s": s.metrics.pageins_per_s,
                        "top_cpu_processes": s.metrics.top_cpu_processes,
                        "top_pagein_processes": s.metrics.top_pagein_processes,
                    },
                }
                for s in samples[-30:]  # Last 3 seconds at 100ms
            ],
            "tier": latest.tier if latest else 1,
            "current_stress": asdict(latest.stress) if latest else None,
            "sample_count": len(samples),
        }

        data = json.dumps(message).encode() + b"\n"
        writer.write(data)
        await writer.drain()
