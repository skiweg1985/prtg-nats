"""Server-sent events: the parts every stream in the API shares.

The job stream and the sensor log stream differ in what they replay and
when they end, not in how they wait, keep the connection alive or frame an
event. That part lives here once, because each of its details was learnt
the hard way and a second copy would only have to learn it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.services.events import StreamEvent

# Long enough to cost nothing, short enough that proxies and browsers do not
# give up on an idle stream.
SSE_KEEPALIVE_SECONDS = 20.0

KEEPALIVE = b": keepalive\n\n"
END = b"event: end\ndata: {}\n\n"


def sse(event: str, payload: dict[str, Any]) -> bytes:
    """One event, framed."""
    body = json.dumps(payload, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


async def follow(
    request: Request, queue: asyncio.Queue[StreamEvent]
) -> AsyncGenerator[StreamEvent | None, None]:
    """What arrives on ``queue`` until the client goes, None at every idle
    interval so the caller can send a keepalive.

    The wait for the next event is a task of its own, outliving the
    keepalive. wait_for cancels what it waits on when it times out, and a
    cancelled get() would take the queued event with it: the stream would
    then lose an event on every idle interval instead of sending a comment
    and carrying on. Use it under contextlib.aclosing, so that leaving the
    loop early cancels that task at once rather than whenever the generator
    is collected.
    """
    pending: asyncio.Task[StreamEvent] = asyncio.ensure_future(queue.get())
    try:
        while True:
            if await request.is_disconnected():
                return
            done, _ = await asyncio.wait({pending}, timeout=SSE_KEEPALIVE_SECONDS)
            if not done:
                yield None
                continue
            update = pending.result()
            pending = asyncio.ensure_future(queue.get())
            yield update
    finally:
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending


def event_stream(body: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers event streams into uselessness without this.
            "X-Accel-Buffering": "no",
        },
    )
