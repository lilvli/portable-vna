from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

from .base import TransportClosed, TransportError

WriteHook = Callable[[bytes], Awaitable[None] | None]
LifecycleHook = Callable[[], Awaitable[None] | None]

_CLOSED = object()


class FakeTransport:
    """In-memory byte transport for deterministic, real-resource-free tests."""

    def __init__(
        self,
        *,
        read_timeout_s: float = 0.01,
        on_write: WriteHook | None = None,
        on_open: LifecycleHook | None = None,
        on_close: LifecycleHook | None = None,
    ) -> None:
        if read_timeout_s <= 0:
            raise ValueError("read_timeout_s must be positive")
        self.read_timeout_s = read_timeout_s
        self.on_write = on_write
        self.on_open = on_open
        self.on_close = on_close
        self.writes: list[bytes] = []
        self.open_count = 0
        self.close_count = 0
        self._is_open = False
        self._rx: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._read_buffer = bytearray()

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def open(self) -> None:
        if self._is_open:
            raise TransportError("fake transport is already open")
        self._rx = asyncio.Queue()
        self._read_buffer.clear()
        self._is_open = True
        self.open_count += 1
        await self._run_hook(self.on_open)

    async def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        self.close_count += 1
        await self._run_hook(self.on_close)
        self._rx.put_nowait(_CLOSED)

    async def write(self, data: bytes) -> None:
        self._require_open()
        wire = bytes(data)
        self.writes.append(wire)
        hook = self.on_write
        if hook is not None:
            result = hook(wire)
            if inspect.isawaitable(result):
                await result

    async def read(self, max_bytes: int = 4096) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._require_open()
        if not self._read_buffer:
            try:
                item = await asyncio.wait_for(self._rx.get(), timeout=self.read_timeout_s)
            except TimeoutError:
                return b""
            if item is _CLOSED:
                raise TransportClosed("fake transport closed during read")
            self._read_buffer.extend(item)
        result = bytes(self._read_buffer[:max_bytes])
        del self._read_buffer[:max_bytes]
        return result

    async def inject_rx(self, data: bytes, *, chunks: tuple[int, ...] | None = None) -> None:
        """Inject device bytes, optionally split into an exact repeating chunk pattern."""

        self._require_open()
        wire = bytes(data)
        if not chunks:
            if wire:
                self._rx.put_nowait(wire)
            return
        if not chunks or any(size <= 0 for size in chunks):
            raise ValueError("chunk sizes must be positive")
        offset = 0
        index = 0
        while offset < len(wire):
            size = chunks[index % len(chunks)]
            self._rx.put_nowait(wire[offset : offset + size])
            offset += size
            index += 1

    def clear_writes(self) -> None:
        self.writes.clear()

    def _require_open(self) -> None:
        if not self._is_open:
            raise TransportClosed("fake transport is not open")

    @staticmethod
    async def _run_hook(hook: LifecycleHook | None) -> None:
        if hook is None:
            return
        result = hook()
        if inspect.isawaitable(result):
            await result
