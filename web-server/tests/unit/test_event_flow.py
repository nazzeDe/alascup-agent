import pytest

from src.agent.events import EventChannel, EventFlow, ReasoningDelta

pytestmark = pytest.mark.asyncio


async def test_event_flow_drains_queued_events_after_producer_closes_channel():
    async def producer(channel: EventChannel) -> None:
        await channel.send(ReasoningDelta("late reasoning"))
        channel.close()

    flow = EventFlow(producer)

    events = [event async for event in flow.events()]

    assert events == [ReasoningDelta("late reasoning")]


async def test_event_flow_surfaces_producer_exception_after_draining_events():
    async def producer(channel: EventChannel) -> None:
        await channel.send(ReasoningDelta("before crash"))
        raise RuntimeError("producer crashed")

    flow = EventFlow(producer)
    events = []

    with pytest.raises(RuntimeError, match="producer crashed"):
        async for event in flow.events():
            events.append(event)

    assert events == [ReasoningDelta("before crash")]
