"""Tests for SSE drain_queue — ensures streaming deltas survive across multiple think cycles."""

import asyncio
import json

import pytest

pytestmark = pytest.mark.asyncio


async def _simulate_drain(source: asyncio.Queue, sink: asyncio.Queue, break_on_thinking_done: bool) -> None:
    """Replicate the drain_queue coroutine from query.py.

    When *break_on_thinking_done* is True, this is the buggy version (breaks after
    first thinking_done). When False, this is the fixed version (forwards everything
    until cancelled).
    """
    while True:
        item = await source.get()
        if break_on_thinking_done and item.get("event") == "thinking_done":
            await sink.put(item)
            break
        await sink.put(item)


async def _drain_sink(sink: asyncio.Queue) -> list[dict]:
    """Read all items from sink with a short timeout."""
    items: list[dict] = []
    while True:
        try:
            item = await asyncio.wait_for(sink.get(), timeout=0.3)
            items.append(item)
        except asyncio.TimeoutError:
            break
    return items


class TestDrainQueueIsolation:
    """Unit tests for drain_queue in isolation (no graph, no LLM)."""

    async def test_buggy_version_loses_second_cycle_events(self):
        """Reproduce: drain_queue breaks after first thinking_done, loses cycle 2."""
        source = asyncio.Queue()
        sink = asyncio.Queue()

        # Cycle 1: reasoning → assistant delta → thinking_done
        await source.put({"event": "reasoning", "data": json.dumps({"delta": "Hmm"})})
        await source.put({"event": "assistant", "data": json.dumps({"delta": "Let me"})})
        await source.put({"event": "thinking_done"})

        # Cycle 2 (post-approval): more streaming events
        await source.put({"event": "reasoning", "data": json.dumps({"delta": "After tool"})})
        await source.put({"event": "assistant", "data": json.dumps({"delta": "result is"})})
        await source.put({"event": "thinking_done"})

        task = asyncio.create_task(_simulate_drain(source, sink, break_on_thinking_done=True))

        # Wait a bit for drain to process
        await asyncio.sleep(0.1)

        items = await _drain_sink(sink)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [it.get("event") for it in items]

        # Buggy: only first 3 items visible; cycle 2 items lost
        assert "reasoning" in events
        assert "assistant" in events
        assert "thinking_done" in events

        # The bug: cycle 2 events are never forwarded
        cycle2_events = [e for e in events if "After tool" in str(e) or "result is" in str(e)]
        assert len(cycle2_events) == 0, (
            f"BUG CONFIRMED: cycle-2 streaming events lost. "
            f"Got {len(items)} items: {events}"
        )

    async def test_fixed_version_forwards_all_cycle_events(self):
        """Verify fix: drain_queue forwards events from ALL cycles."""
        source = asyncio.Queue()
        sink = asyncio.Queue()

        # Cycle 1
        await source.put({"event": "reasoning", "data": json.dumps({"delta": "Hmm"})})
        await source.put({"event": "assistant", "data": json.dumps({"delta": "Let me"})})
        await source.put({"event": "thinking_done"})

        # Cycle 2 (post-approval)
        await source.put({"event": "reasoning", "data": json.dumps({"delta": "After tool"})})
        await source.put({"event": "assistant", "data": json.dumps({"delta": "result is"})})
        await source.put({"event": "thinking_done"})

        task = asyncio.create_task(_simulate_drain(source, sink, break_on_thinking_done=False))

        await asyncio.sleep(0.15)

        items = await _drain_sink(sink)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [it.get("event") for it in items]

        # Fixed: all 6 events forwarded
        assert len(items) >= 6, (
            f"FIX EXPECTED: all 6 events forwarded. Got {len(items)}: {events}"
        )
        assert events == [
            "reasoning", "assistant", "thinking_done",
            "reasoning", "assistant", "thinking_done",
        ], f"Event sequence mismatch: {events}"

        # Verify cycle 2 deltas specifically
        assistant_deltas = [
            json.loads(it["data"]).get("delta", "")
            for it in items if it.get("event") == "assistant"
        ]
        assert "Let me" in assistant_deltas, f"cycle 1 delta lost: {assistant_deltas}"
        assert "result is" in assistant_deltas, f"cycle 2 delta lost: {assistant_deltas}"

    async def test_drain_blocks_between_cycles_no_busy_wait(self):
        """drain_queue should block on queue.get() between cycles (no spin)."""
        source = asyncio.Queue()
        sink = asyncio.Queue()

        await source.put({"event": "reasoning", "data": json.dumps({"delta": "Hmm"})})
        await source.put({"event": "thinking_done"})

        task = asyncio.create_task(_simulate_drain(source, sink, break_on_thinking_done=False))

        await asyncio.sleep(0.05)
        items_before = await _drain_sink(sink)
        assert len(items_before) == 2, f"Expected 2 items before gap: {len(items_before)}"

        # Nothing in source → drain blocks → sink empty
        await asyncio.sleep(0.1)
        empty_check = await _drain_sink(sink)
        assert len(empty_check) == 0, (
            f"drain should block when source is empty, but got: {len(empty_check)} items"
        )

        # Now add cycle 2 events
        await source.put({"event": "assistant", "data": json.dumps({"delta": "after"})})
        await source.put({"event": "thinking_done"})

        await asyncio.sleep(0.05)
        items_after = await _drain_sink(sink)
        assert len(items_after) >= 2, (
            f"Expected ≥2 items after cycle 2: got {len(items_after)}"
        )

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class MockRuleEngine:
    @staticmethod
    def evaluate(tool_name, is_read_only, is_rollbackable):
        if "blacklist" in tool_name:
            return "REJECT"
        if is_read_only:
            return "AUTO_APPROVE"
        return "NEEDS_APPROVAL"


class TestDrainQueueIntegration:
    """Integration-level test: drain_queue survives approval → second LLM call."""

    @pytest.fixture
    def bridge(self):
        from src.security.pending import ApprovalBridge
        return ApprovalBridge()

    @pytest.fixture
    def audit(self):
        class _Audit:
            def __init__(self):
                self.events: list = []
            async def log(self, event):
                self.events.append(event)
        return _Audit()

    @pytest.fixture
    def context_manager(self):
        class _Ctx:
            def count_tokens(self, messages):
                return len(str(messages))
            def needs_compression(self, tokens):
                return False
            async def compress(self, messages):
                return messages
        return _Ctx()

    @pytest.fixture
    async def mock_services(self):
        """Provide minimal mocks for Query.chat()."""
        from uuid import UUID, uuid4
        from src.models.message import Message

        class MockSession:
            def __init__(self, sid: UUID):
                self.id = sid
                self.messages: list = []
                self.title: str | None = None

        class MockSessionManager:
            def __init__(self):
                self.sessions: dict[UUID, MockSession] = {}
                self.saved: list[Message] = []

            async def create_session(self):
                sid = uuid4()
                self.sessions[sid] = MockSession(sid)
                return self.sessions[sid]

            async def get_session(self, sid: UUID):
                if sid not in self.sessions:
                    self.sessions[sid] = MockSession(sid)
                return self.sessions[sid]

            async def set_title(self, sid: UUID, title: str):
                s = self.sessions.get(sid)
                if s:
                    s.title = title

            async def add_message(self, sid: UUID, msg: Message):
                s = self.sessions.get(sid)
                if s:
                    s.messages.append(msg)
                self.saved.append(msg)

        class MockPromptManager:
            def build_system_prompt(self):
                return "You are a helpful assistant."

        return {
            "session_manager": MockSessionManager(),
            "prompt_manager": MockPromptManager(),
        }

    async def test_drain_queue_survives_approval_cycle(
        self, mock_services, bridge, context_manager, audit
    ):
        """Full chat() flow with tool approval: verify streaming deltas in all cycles."""
        from src.agent.graph import build_graph
        from src.agent.query import Query
        from src.security.pending import ApprovalBridge

        # LLM that produces: think → tool_call → (after approval) think → text
        class MultiCycleLLM:
            def __init__(self):
                self._call = 0
                self.escalated = False
                self.fallback_switched = False

            async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
                self._call += 1
                if self._call == 1:
                    # First think: tool call for restart (needs approval)
                    yield {
                        "event": "assistant",
                        "data": json.dumps({"delta": "I need to"}),
                    }
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "function": {"name": "restart_service", "arguments": "{}"}
                        }),
                    }
                    yield {"event": "done", "data": "{}"}
                else:
                    # Second think (post-approval): text response
                    yield {
                        "event": "assistant",
                        "data": json.dumps({"delta": "Service"}),
                    }
                    yield {
                        "event": "assistant",
                        "data": json.dumps({"delta": " restarted."}),
                    }
                    yield {"event": "done", "data": "{}"}

            def escalate_max_tokens(self):
                self.escalated = True

            def switch_to_fallback(self):
                self.fallback_switched = True

        class RiskyExecutor:
            def __init__(self):
                self.calls: list[dict] = []

            def list_tools(self):
                return [
                    {"name": "restart_service", "server_name": "tool-server",
                     "mutable": True, "is_read_only": False}
                ]

            async def execute(self, tool_name, arguments, **kwargs):
                self.calls.append({"tool_name": tool_name, "arguments": arguments})
                return {"execution_status": "SUCCEEDED", "output": "done"}

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                results = []
                for c in calls:
                    self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
                    results.append({"tool_call_id": c.get("call_id", ""), "result": {
                        "execution_status": "SUCCEEDED", "output": "done",
                    }})
                return results

        llm = MultiCycleLLM()
        executor = RiskyExecutor()
        rule_engine = MockRuleEngine()
        graph = build_graph(
            llm=llm, executor=executor,
            rule_engine=rule_engine, audit_logger=audit,
        )

        agent = Query(
            llm=llm,
            graph=graph,
            context_manager=context_manager,
            session_manager=mock_services["session_manager"],
            prompt_manager=mock_services["prompt_manager"],
            tool_executor=executor,
            pending_approvals=bridge,
            audit_logger=audit,
        )

        events: list[tuple] = []

        async def collect_and_approve():
            gen = agent.chat("restart the service")
            async for e in gen:
                ev_data = e.get("data", "{}")
                parsed = json.loads(ev_data) if isinstance(ev_data, str) else ev_data
                events.append((e["event"], parsed))
                if e["event"] == "tool_approval_required":
                    bridge.complete(parsed["request_id"], "APPROVED")

        await collect_and_approve()

        event_types = [e[0] for e in events]

        # Should have 2 thinking_done events (one per cycle)
        thinking_done_count = event_types.count("thinking_done")
        assert thinking_done_count >= 2, (
            f"Expected ≥2 thinking_done events (one per cycle), got {thinking_done_count}. "
            f"Events: {event_types}"
        )

        # Cycle 1 streaming deltas (before thinking_done #1)
        first_td_idx = event_types.index("thinking_done")
        cycle1_assistant = [
            e for e in events[:first_td_idx] if e[0] == "assistant"
        ]
        assert len(cycle1_assistant) >= 1, (
            f"Cycle 1 should have streaming assistant deltas. Events before first td: "
            f"{event_types[:first_td_idx]}"
        )

        # Cycle 2 streaming deltas (between thinking_done #1 and #2)
        second_td_idx = (
            event_types.index("thinking_done", first_td_idx + 1)
            if "thinking_done" in event_types[first_td_idx + 1:]
            else len(event_types)
        )
        cycle2_assistant = [
            e for e in events[first_td_idx:second_td_idx] if e[0] == "assistant"
        ]
        assert len(cycle2_assistant) >= 2, (
            f"BUG: Cycle 2 should have ≥2 streaming assistant deltas, got {len(cycle2_assistant)}. "
            f"Events between td1 and td2: {event_types[first_td_idx:second_td_idx]}"
        )

        # Verify tool call was executed
        assert executor.calls, "Expected executor to be called"
