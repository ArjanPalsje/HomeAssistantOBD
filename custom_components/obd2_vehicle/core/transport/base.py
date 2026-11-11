"""Transport abstraction.

A transport moves raw bytes between the integration and an ELM327-compatible
adapter. It knows nothing about OBD. New adapter types (Bluetooth Classic,
USB serial, other BLE layouts) only need a new :class:`Transport` subclass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

DataCallback = Callable[[bytes], None]
DisconnectCallback = Callable[[], None]


class Transport(ABC):
    """Byte stream to an ELM327 compatible adapter."""

    #: Human readable transport kind, used in diagnostics.
    kind: str = "abstract"

    def __init__(self) -> None:
        self._data_callback: DataCallback | None = None
        self._disconnect_callback: DisconnectCallback | None = None

    def set_data_callback(self, callback: DataCallback | None) -> None:
        """Register the callback receiving every chunk of incoming bytes."""
        self._data_callback = callback

    def set_disconnect_callback(self, callback: DisconnectCallback | None) -> None:
        """Register a callback fired when the link drops unexpectedly."""
        self._disconnect_callback = callback

    def _emit_data(self, data: bytes) -> None:
        if self._data_callback is not None and data:
            self._data_callback(data)

    def _emit_disconnect(self) -> None:
        if self._disconnect_callback is not None:
            self._disconnect_callback()

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True while the link is up."""

    @abstractmethod
    async def connect(self, timeout: float) -> None:
        """Open the link. Raises TransportUnavailable / TransportError."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the link. Must never raise."""

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Write raw bytes to the adapter."""

    def diagnostics(self) -> dict[str, object]:
        """Return transport details for diagnostics (no secrets)."""
        return {"kind": self.kind, "connected": self.is_connected}
