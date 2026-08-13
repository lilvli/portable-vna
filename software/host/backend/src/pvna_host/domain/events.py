from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .models import utc_now


@dataclass(frozen=True, slots=True)
class Event:
    event_id: int
    event: str
    data: dict[str, Any]
    run_id: str | None = None
    timestamp_utc: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": "pvna.events.v1",
            "event_id": self.event_id,
            "event": self.event,
            "timestamp_utc": self.timestamp_utc,
            "data": self.data,
        }
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        return payload


class EventBroker:
    def __init__(self) -> None:
        self._next_id = 1
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._recent: deque[Event] = deque(maxlen=200)
        self._lock = asyncio.Lock()

    async def publish(
        self, event: str, data: dict[str, Any], *, run_id: str | None = None
    ) -> Event:
        async with self._lock:
            item = Event(self._next_id, event, data, run_id, utc_now())
            self._next_id += 1
            self._recent.append(item)
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(item)
        return item

    async def subscribe(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def recent(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [event.to_api_dict() for event in self._recent]
