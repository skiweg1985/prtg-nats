"""In-process fan-out for live job output.

One server, one process, so a broadcaster in memory is enough - Redis would add
an operational dependency to solve a problem this deployment does not have. If
the platform ever runs on several nodes, this interface is the one seam that
has to grow a backend; nothing else changes.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# A subscriber that cannot keep up loses the oldest lines rather than stalling
# the job that produces them. The detail page refetches on reconnect, so a gap
# is a redraw, not lost data.
SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(slots=True)
class StreamEvent:
    topic: str
    kind: str  # "job.event" | "job.status" | "job.step"
    payload: dict[str, Any] = field(default_factory=dict)


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamEvent]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: StreamEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(event.topic, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    async def register(self, topic: str) -> asyncio.Queue[StreamEvent]:
        """Start collecting for a subscriber that is not reading yet.

        Split out of subscribe() for the one caller that has to be listening
        before it reads anything: a stream replaying stored history first has
        to hold the live events that arrive while it does, or the lines
        produced between the read and the first yield exist in neither half.
        The caller owns the queue until it hands it back to unregister().
        """
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(SUBSCRIBER_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.setdefault(topic, set()).add(queue)
        return queue

    async def unregister(self, topic: str, queue: asyncio.Queue[StreamEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(topic)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    del self._subscribers[topic]


def job_topic(job_id: str) -> str:
    return f"job:{job_id}"


_broadcaster = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    return _broadcaster
