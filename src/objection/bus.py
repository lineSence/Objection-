"""In-process event bus: live events for SSE subscribers (Web UI)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from .schemas import Event


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[Event]) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, event: Event) -> None:
        for q in list(self._subs.get(event.run_id, ())):
            q.put_nowait(event)
