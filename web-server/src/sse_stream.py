"""SSEStream — SSE 流的完整抽象。

封裝 session 創建、agent 執行、SSE 事件發射、持久化、資源清理為一個 async
iterable。外部只需 ``async for``。

契約：第一事件永遠是 ``session_init`` 攜帶 chat_id；最後事件永遠是 ``done``。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger

from src.agent.query import AgentRunner
from src.models.message import Message, MessageType


class SSEStream:
    """SSE 流的完整抽象。封裝從 session 創建到清理的完整生命週期。

    用法::

        stream = SSEStream(
            user_message="...",
            chat_id=None,           # None → 建立新 session
            session_manager=sm,
            prompt_manager=pm,
            query=query,            # Query 實例（已注入所有依賴）
            tool_executor=exec,
            disconnect_check=lambda: request.is_disconnected(),  # optional
        )
        async for event in stream:
            # event 格式: {"event": str, "data": str}
            ...

    契約：
    - 第一個事件永遠是 ``session_init``，攜帶真實 chat_id
    - 最後一個事件永遠是 ``done``
    - ``disconnect_check`` 若提供，在每次 yield 後呼叫；若返回 True 則中止並 yield ``done``
    """

    def __init__(
        self,
        *,
        user_message: str,
        chat_id: str | None,
        session_manager,
        prompt_manager,
        query: AgentRunner,
        tool_executor,
        disconnect_check=None,
    ):
        self._user_message = user_message
        self._chat_id = chat_id
        self._session_manager = session_manager
        self._prompt_manager = prompt_manager
        self._query = query
        self._tool_executor = tool_executor
        self._disconnect_check = disconnect_check  # callable → bool|coroutine, for HTTP disconnect detection

    async def _is_disconnected(self) -> bool:
        """Call the disconnect check, supporting both sync and async callables."""
        if not self._disconnect_check:
            return False
        result = self._disconnect_check()
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)

    async def __aiter__(self):
        """執行完整 chat turn 並 yield SSE 事件。"""
        # ── 1. Session management ──────────────────────────────────────────
        if self._chat_id is None:
            session = await self._session_manager.create_session()
        else:
            session = await self._session_manager.get_session(UUID(self._chat_id))

        chat_id_uuid = session.id

        # ── 2. Yield session_init ──────────────────────────────────────────
        yield {"event": "session_init", "data": json.dumps({"chat_id": str(chat_id_uuid)})}
        if await self._is_disconnected():
            yield {"event": "done", "data": "{}"}
            return

        # ── 3. Build history from session messages ─────────────────────────
        history: list[dict] = []
        for m in session.messages:
            if m.is_meta:
                continue
            role = m.type.value if isinstance(m.type, MessageType) else m.type
            history.append({"role": role, "content": m.content})

        # ── 4. Set title ───────────────────────────────────────────────────
        if not session.title and self._user_message:
            title = self._user_message.split("\n")[0][:20]
            await self._session_manager.set_title(chat_id_uuid, title)

        # ── 5. Persist user message ────────────────────────────────────────
        user_msg = Message(
            message_id=uuid4(),
            chat_id=chat_id_uuid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content=self._user_message,
        )
        await self._session_manager.add_message(chat_id_uuid, user_msg)

        # ── 6. Build system prompt ─────────────────────────────────────────
        system_prompt = self._prompt_manager.build_system_prompt()

        # Update query's chat_id so orchestrator audit logging uses the real UUID.
        self._query._chat_id = str(chat_id_uuid)

        # ── 7. Event queue for graph-node streaming ───────────────────────
        queue: asyncio.Queue = asyncio.Queue()

        # ── 8. Agent execution + event relay ───────────────────────────────
        available_tools = self._tool_executor.list_tools()
        messages = history + [{"role": "user", "content": self._user_message}]

        event_queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        async def drain_queue():
            """Forward reasoning and assistant streaming events in real-time.

            Runs until cancelled by the outer finally block.  Does NOT break
            on thinking_done — the orchestrator may invoke the graph multiple
            times (e.g. after tool approval), producing multiple think cycles.
            """
            while True:
                item = await queue.get()
                if "data" not in item:
                    item["data"] = "{}"
                await event_queue.put(("item", item))

        async def run_agent():
            """Drive the agent iterator and forward events."""
            try:
                async for event in self._query.run(messages, available_tools, system=system_prompt,
                                                      _event_queue=queue, _chat_id=str(chat_id_uuid)):
                    await event_queue.put(("agent", event))
            except Exception as exc:
                logger.opt(exception=True).error(
                    "agent_run_failed chat_id={c}", c=str(chat_id_uuid)
                )
                error_event = {
                    "event": "error",
                    "data": json.dumps(
                        {"code": "AGENT_CRASH", "message": str(exc)}, default=str
                    ),
                }
                await event_queue.put(("agent", error_event))
                await event_queue.put(("agent", {"event": "done", "data": "{}"}))
            finally:
                await event_queue.put(("done", None))

        drain_task = asyncio.create_task(drain_queue())
        agent_task = asyncio.create_task(run_agent())
        agent_done = False
        disconnected = False

        collected_text: list[str] = []

        try:
            while not agent_done:
                try:
                    tag, event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if tag == "done":
                    agent_done = True
                    continue

                # Collect assistant delta for persistence.
                if event.get("event") == "assistant":
                    try:
                        data = json.loads(event["data"])
                        if data.get("message_id"):
                            collected_text.append(data.get("delta", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass

                yield event

                if await self._is_disconnected():
                    disconnected = True
                    break

            # Drain remaining items after agent completes (or disconnect).
            while not event_queue.empty():
                tag, event = event_queue.get_nowait()
                if event:
                    yield event

            if not disconnected:
                # ── 9. Persist assistant message ───────────────────────────
                full_text = "".join(collected_text)
                if full_text:
                    assistant_msg = Message(
                        message_id=uuid4(),
                        chat_id=chat_id_uuid,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        type=MessageType.ASSISTANT,
                        content=full_text,
                    )
                    await self._session_manager.add_message(chat_id_uuid, assistant_msg)
            else:
                yield {"event": "done", "data": "{}"}

        except Exception as exc:
            logger.opt(exception=True).error(
                "sse_stream_crash chat_id={c}", c=str(chat_id_uuid)
            )
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": "SSE_CRASH", "message": str(exc)}, default=str
                ),
            }
            yield {"event": "done", "data": "{}"}

        finally:
            # ── 10. Cleanup ────────────────────────────────────────────────
            agent_task.cancel()
            drain_task.cancel()
            for t in (agent_task, drain_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass


