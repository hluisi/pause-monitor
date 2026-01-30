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
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pause_monitor.collector import ProcessSamples
    from pause_monitor.ringbuffer import RingBuffer

log = structlog.get_logger()


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
        log.info("socket_server_started", path=str(self.socket_path))

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

        log.info("socket_server_stopped", path=str(self.socket_path))

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

    def _handle_log_message(self, msg: dict) -> None:
        """Handle a log message from TUI.

        TUI sends log messages via the socket connection, which the daemon
        writes to the unified log file with source="tui".

        Args:
            msg: Log message dict with level, event, and optional fields
        """
        level = msg.get("level", "info")
        event = msg.get("event", "tui_log")

        # Extract extra fields (everything except type, level, event)
        extra = {k: v for k, v in msg.items() if k not in ("type", "level", "event")}

        # Log at appropriate level with source=tui (added by processor)
        log_method = getattr(log, level, log.info)
        log_method(event, source="tui", **extra)

    def _handle_client_message(self, msg: dict) -> None:
        """Route incoming message to appropriate handler.

        Args:
            msg: Parsed JSON message from client
        """
        msg_type = msg.get("type")

        if msg_type == "log":
            self._handle_log_message(msg)
        # Add other message types here as needed

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection.

        Bidirectional communication:
        - Daemon → TUI: broadcasts via broadcast() method
        - TUI → Daemon: receives JSON messages (type: "log", etc.)
        """
        self._clients.add(writer)
        log.info("tui_connected", clients=len(self._clients))

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

            # Read loop: process incoming messages from client
            while self._running:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                    if not line:
                        break  # Client disconnected

                    # Parse and handle the message
                    try:
                        msg = json.loads(line.decode())
                        self._handle_client_message(msg)
                    except json.JSONDecodeError:
                        log.warning("invalid_client_message", raw=line[:100])

                except TimeoutError:
                    continue  # No message, check running flag and loop
                except ConnectionError:
                    break
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("tui_disconnected", clients=len(self._clients))
