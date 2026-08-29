"""A subscriber has to be listening before it reads the stored half.

The live log is served in two pieces: what the database already holds, and
what arrives after. Reading the first and then subscribing leaves a window -
the worker keeps logging while the backlog is read, the response is assembled
and the ASGI server gets around to calling the generator - and every line
written in there was in neither piece. register() exists to close it.
"""

from __future__ import annotations

from app.services.events import SUBSCRIBER_QUEUE_SIZE, EventBroadcaster, StreamEvent


def _event(sequence: int) -> StreamEvent:
    return StreamEvent(topic="job:x", kind="job.event", payload={"sequence": sequence})


async def test_registering_collects_before_anyone_reads() -> None:
    broadcaster = EventBroadcaster()
    queue = await broadcaster.register("job:x")

    # Stands in for the worker logging while the endpoint reads the backlog.
    await broadcaster.publish(_event(1))
    await broadcaster.publish(_event(2))

    assert queue.get_nowait().payload["sequence"] == 1
    assert queue.get_nowait().payload["sequence"] == 2
    await broadcaster.unregister("job:x", queue)


async def test_unregistering_stops_delivery_and_drops_the_topic() -> None:
    broadcaster = EventBroadcaster()
    queue = await broadcaster.register("job:x")

    await broadcaster.unregister("job:x", queue)
    await broadcaster.publish(_event(1))

    assert queue.empty()
    # Reaching into the private map: the public reader this used to go
    # through existed only for this assertion.
    assert "job:x" not in broadcaster._subscribers


async def test_unregistering_one_of_two_leaves_the_other_listening() -> None:
    broadcaster = EventBroadcaster()
    staying = await broadcaster.register("job:x")
    leaving = await broadcaster.register("job:x")

    await broadcaster.unregister("job:x", leaving)
    await broadcaster.publish(_event(1))

    assert staying.get_nowait().payload["sequence"] == 1
    assert leaving.empty()
    await broadcaster.unregister("job:x", staying)


async def test_a_subscriber_that_cannot_keep_up_loses_the_oldest_line() -> None:
    """Documented behaviour: the job must not stall on a slow reader."""
    broadcaster = EventBroadcaster()
    queue = await broadcaster.register("job:x")

    for sequence in range(SUBSCRIBER_QUEUE_SIZE + 1):
        await broadcaster.publish(_event(sequence))

    assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE
    assert queue.get_nowait().payload["sequence"] == 1
    await broadcaster.unregister("job:x", queue)
