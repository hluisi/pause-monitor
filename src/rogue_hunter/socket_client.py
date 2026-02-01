# src/rogue_hunter/socket_client.py

"""Unix socket client for receiving ring buffer data from daemon."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable


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

        self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))

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

    def close(self) -> None:
        """Close the socket synchronously (doesn't wait for clean shutdown).

        Use this to interrupt blocking reads before cancelling tasks.
        """
        if self._writer:
            self._writer.close()

    async def read_message(self, timeout: float = 1.0) -> dict[str, Any]:
        """Read next message from socket with timeout.

        Args:
            timeout: Max seconds to wait for data (default 1.0)

        Returns:
            Parsed JSON message from daemon

        Raises:
            ConnectionError: If connection is lost
            TimeoutError: If no data received within timeout
            json.JSONDecodeError: If message is invalid JSON
        """
        if not self._reader:
            raise ConnectionError("Not connected")

        line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if not line:
            raise ConnectionError("Connection closed by server")

        return json.loads(line.decode())

    async def send_message(self, msg: dict[str, Any]) -> None:
        """Send a message to the daemon.

        Messages are JSON-encoded with a newline delimiter.

        Args:
            msg: Dictionary to send (must be JSON-serializable)

        Raises:
            ConnectionError: If not connected or write fails
        """
        if not self._writer or self._writer.is_closing():
            raise ConnectionError("Not connected")

        try:
            data = json.dumps(msg).encode() + b"\n"
            self._writer.write(data)
            await self._writer.drain()
        except Exception as e:
            raise ConnectionError(f"Send failed: {e}") from e
