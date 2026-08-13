from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .base import TransportClosed, TransportError

SerialFactory = Callable[..., Any]


class SerialTransport:
    """Explicitly opened pyserial transport for the PVNA-Link 8N1 baseline.

    Construction only records configuration.  It neither enumerates ports nor
    imports/opens pyserial; the first real resource access is :meth:`open`.
    """

    SUPPORTED_BAUDRATES = frozenset({115_200, 921_600})

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115_200,
        read_timeout_s: float = 0.05,
        write_timeout_s: float = 0.5,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        if not port or not port.strip():
            raise ValueError("an explicit serial port is required")
        if baudrate not in self.SUPPORTED_BAUDRATES:
            raise ValueError("PVNA-Link V0.1 supports 115200 or 921600 baud")
        if read_timeout_s <= 0 or write_timeout_s <= 0:
            raise ValueError("serial timeouts must be positive")
        self.port = port
        self.baudrate = baudrate
        self.read_timeout_s = read_timeout_s
        self.write_timeout_s = write_timeout_s
        self._serial_factory = serial_factory
        self._serial: Any | None = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and getattr(self._serial, "is_open", False))

    async def open(self) -> None:
        if self.is_open:
            raise TransportError("serial transport is already open")
        factory = self._serial_factory
        if factory is None:
            import serial

            factory = serial.Serial
        try:
            serial_port = await asyncio.to_thread(
                factory,
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self.read_timeout_s,
                write_timeout=self.write_timeout_s,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except Exception as exc:
            raise TransportError(f"could not open serial port {self.port!r}: {exc}") from exc
        if not getattr(serial_port, "is_open", False):
            try:
                await asyncio.to_thread(serial_port.open)
            except Exception as exc:
                raise TransportError(f"could not open serial port {self.port!r}: {exc}") from exc
        self._serial = serial_port

    async def close(self) -> None:
        serial_port, self._serial = self._serial, None
        if serial_port is None:
            return
        try:
            if getattr(serial_port, "is_open", False):
                await asyncio.to_thread(serial_port.close)
        except Exception as exc:
            raise TransportError(f"could not close serial port {self.port!r}: {exc}") from exc

    async def write(self, data: bytes) -> None:
        serial_port = self._require_open()
        if not data:
            return
        try:
            written = await asyncio.to_thread(serial_port.write, data)
        except Exception as exc:
            raise TransportError(f"serial write failed: {exc}") from exc
        if written != len(data):
            raise TransportError(f"short serial write: {written} of {len(data)} bytes")

    async def read(self, max_bytes: int = 4096) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        serial_port = self._require_open()
        try:
            data = await asyncio.to_thread(serial_port.read, max_bytes)
        except Exception as exc:
            raise TransportError(f"serial read failed: {exc}") from exc
        if not self.is_open:
            raise TransportClosed("serial port closed during read")
        return bytes(data)

    def _require_open(self) -> Any:
        if not self.is_open:
            raise TransportClosed("serial transport is not open")
        return self._serial
