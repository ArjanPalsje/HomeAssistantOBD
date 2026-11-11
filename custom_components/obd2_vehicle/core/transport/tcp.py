"""TCP transport for Wi-Fi ELM327 adapters (e.g. Vgate iCar 2 Wi-Fi, 192.168.0.10:35000)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ..exceptions import TransportError, TransportUnavailable
from .base import Transport

_LOGGER = logging.getLogger(__name__)


class TcpTransport(Transport):
    """Plain TCP socket transport."""

    kind = "tcp"

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, timeout: float) -> None:
        self._closing = False
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout
            )
        except (TimeoutError, OSError) as err:
            raise TransportUnavailable(
                f"Cannot connect to {self._host}:{self._port}: {err}"
            ) from err
        self._read_task = asyncio.create_task(self._read_loop(), name="obd2_tcp_reader")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                data = await self._reader.read(1024)
                if not data:
                    break
                self._emit_data(data)
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.debug("TCP read error: %s", err)
        except asyncio.CancelledError:
            raise
        finally:
            if not self._closing:
                self._writer = None
                self._emit_disconnect()

    async def disconnect(self) -> None:
        self._closing = True
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
            self._read_task = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
        self._writer = None
        self._reader = None

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise TransportError("Not connected")
        try:
            self._writer.write(data)
            await self._writer.drain()
        except OSError as err:
            raise TransportError(f"TCP write failed: {err}") from err

    def diagnostics(self) -> dict[str, object]:
        return {**super().diagnostics(), "port": self._port}
