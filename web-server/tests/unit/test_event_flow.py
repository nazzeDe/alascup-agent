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
