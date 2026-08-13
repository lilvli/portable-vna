from __future__ import annotations

from typing import Protocol, runtime_checkable


class TransportError(RuntimeError):
    """The byte transport cannot complete an I/O operation."""


class TransportClosed(TransportError):
    """The byte transport is not open, or closed while an operation was pending."""


@runtime_checkable
class ByteTransport(Protocol):
    """Minimal asynchronous byte-stream contract used by PVNA-Link sessions."""

    @property
    def is_open(self) -> bool: ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def write(self, data: bytes) -> None: ...

    async def read(self, max_bytes: int = 4096) -> bytes: ...
