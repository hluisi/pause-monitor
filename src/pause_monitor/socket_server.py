# src/pause_monitor/socket_server.py
"""Unix socket server for real-time streaming to TUI.

PUSH-BASED DESIGN (per Design Simplifications):
- Main loop calls broadcast() after each sample
- No internal polling loop - data flows directly from daemon
- Protocol: newline-delimited JSON messages
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pause_monitor.collector import ProcessSamples
    from pause_monitor.ringbuffer import RingBuffer

log = logging.getLogger(__name__)


class SocketServer:
    """Unix domain socket server for real-time streaming to TUI.

    PUSH-BASED DESIGN (per Design Simplifications):
    - Main loop calls broadcast() after each sample
    - No internal polling loop - data flows directly from daemon
    - Protocol: newline-delimited JSON messages
    - Message type: 'sample' with current ProcessSamples
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

        # Make socket world-accessible so TUI can connect
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

    async def broadcast(self, samples: ProcessSamples) -> None:
        """Broadcast sample to all connected TUI clients.

        Called from main loop after each sample.
        This is the push-based approach - no internal polling.

        Args:
            samples: ProcessSamples with scored rogues
        """
        if not self._clients:
            return

        message = {
            "type": "sample",
            "timestamp": samples.timestamp.isoformat(),
            "elapsed_ms": samples.elapsed_ms,
            "process_count": samples.process_count,
            "max_score": samples.max_score,
            "rogues": [p.to_dict() for p in samples.rogues],
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
            # Send initial state with ring buffer history for sparkline
            history = [s.samples.max_score for s in self.ring_buffer.samples]
            initial_state = {
                "type": "initial_state",
                "history": history,
                "sample_count": len(history),
            }
            data = json.dumps(initial_state).encode() + b"\n"
            writer.write(data)
            await writer.drain()

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
