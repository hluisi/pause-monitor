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

        self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))
        log.info("socket_client_connected path=%s", self.socket_path)

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
