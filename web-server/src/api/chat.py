import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agent.nodes import _event_queue as reasoning_queue, _chat_id_ctx
from src.agent.query import Query
from src.models.message import Message, MessageType
from src.services.container import (
    approval_bridge,
    audit_logger,
    context_manager,
    error_recovery,
    graph,
    llm_adapter,
    prompt_manager,
    session_manager,
    tool_executor,
)
from src.tools import start_feature, complete_feature, summarize_feature_durations

router = APIRouter()

# Per-chat-id active task tracking for abort-on-new-message semantics.
_active_tasks: dict[str, asyncio.Task] = {}


class ChatTurnRequest(BaseModel):
    message: str
    model: str | None = None
    chat_id: str | None = None


@router.post("/chat")
async def chat_turn(
    body: ChatTurnRequest,
    request: Request,
    session_mgr=Depends(session_manager),
    prompt_mgr=Depends(prompt_manager),
    llm=Depends(llm_adapter),
    context_mgr=Depends(context_manager),
    executor=Depends(tool_executor),
    audit_logger=Depends(audit_logger),
    bridge=Depends(approval_bridge),
    graph_dep=Depends(graph),
    error_rec=Depends(error_recovery),
):
    """Frontend-facing SSE endpoint. Creates session if chat_id not provided."""
    chat_id_str = body.chat_id
    if not chat_id_str:
        new_session = await session_mgr.create_session()
        chat_id_str = str(new_session.id)

    # Abort previous request for this chat (Anthropic Claude pattern).
    if old_task := _active_tasks.get(chat_id_str):
        if not old_task.done():
            old_task.cancel()

    current = asyncio.current_task()
    _active_tasks[chat_id_str] = current

    try:
        return await _do_chat(
            body, request, chat_id_str,
            session_mgr, prompt_mgr, llm, context_mgr,
            executor, audit_logger, bridge, graph_dep,
            error_rec,
        )
    except asyncio.CancelledError:
        raise HTTPException(status_code=499, detail="Request cancelled by newer message")
    finally:
        _active_tasks.pop(chat_id_str, None)


async def _do_chat(
    body: ChatTurnRequest,
    request: Request,
    chat_id_str: str,
    session_mgr,
    prompt_mgr,
    llm,
    context_mgr,
    executor,
    audit_logger,
    bridge,
    graph_dep,
    error_rec,
):
    """Core chat logic, separated for abort wrapping."""
    try:
        chat_id = UUID(chat_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid chat_id")

    try:
        session = await session_mgr.get_session(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")

    history: list[dict] = []
    for m in session.messages:
        if m.is_meta:
            continue
        role = m.type.value if isinstance(m.type, MessageType) else m.type
        history.append({"role": role, "content": m.content})

    user_msg = Message(
        message_id=uuid4(),
        chat_id=chat_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        type=MessageType.USER,
        content=body.message,
    )
    await session_mgr.add_message(chat_id, user_msg)

    if not session.title and body.message:
        title = body.message.split("\n")[0][:20]
        await session_mgr.set_title(chat_id, title)

    system_prompt = prompt_mgr.build_system_prompt()
    agent = Query(
        llm=llm,
        graph=graph_dep,
        context_manager=context_mgr,
        pending_approvals=bridge,
        audit_logger=audit_logger,
        error_recovery=error_rec,
        chat_id=chat_id,
    )

    available_tools = executor.list_tools()
    messages = history + [{"role": "user", "content": body.message}]

    async def event_generator():
        feature = f"chat_turn:{str(chat_id)}"
        start_feature(feature)
        queue: asyncio.Queue = asyncio.Queue()  # unbounded: graph must never block on put
        token = reasoning_queue.set(queue)
        chat_id_token = _chat_id_ctx.set(str(chat_id))

        collected_text: list[str] = []

        # Unified event queue for concurrent drain + agent processing.
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        async def drain_queue():
            """Forward reasoning and assistant streaming events in real-time."""
            while True:
                item = await queue.get()
                if item.get("event") == "thinking_done":
                    break
                await event_queue.put(("item", item))

        async def run_agent():
            """Drive the agent iterator and forward events."""
            try:
                async for event in agent.run(messages, available_tools, system=system_prompt):
                    await event_queue.put(("agent", event))
            finally:
                await event_queue.put(("done", None))

        drain_task = asyncio.create_task(drain_queue())
        agent_task = asyncio.create_task(run_agent())
        agent_done = False

        try:
            while not agent_done:
                if await request.is_disconnected():
                    break

                try:
                    tag, event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if tag == "done":
                    agent_done = True
                    continue

                # Track assistant text from emit_events (formal messages with message_id).
                if event.get("event") == "assistant":
                    try:
                        data = json.loads(event["data"])
                        if data.get("message_id"):
                            collected_text.append(data.get("delta", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass

                yield event

            # Drain any remaining items after agent completes.
            while not event_queue.empty():
                tag, event = event_queue.get_nowait()
                if event:
                    yield event

        finally:
            reasoning_queue.reset(token)
            _chat_id_ctx.reset(chat_id_token)
            agent_task.cancel()
            drain_task.cancel()
            for t in (agent_task, drain_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            complete_feature(feature)
            summarize_feature_durations()

            logger.debug("COLLECTED_TEXT: events={n} text_len={l}",
                         n=len(collected_text), l=sum(len(t) for t in collected_text))
            full_text = "".join(collected_text)
            if full_text:
                assistant_msg = Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    type=MessageType.ASSISTANT,
                    content=full_text,
                )
                await session_mgr.add_message(chat_id, assistant_msg)
                logger.debug("SAVED_ASSISTANT_MSG: chat_id={c} text_len={l}", c=chat_id, l=len(full_text))

    return EventSourceResponse(
        event_generator(), headers={"X-Session-ID": str(chat_id)}
    )
