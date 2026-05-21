import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agent.query import Query
from src.models.message import Message, MessageType
from src.services.container import (
    approval_bridge,
    audit_logger,
    context_manager,
    graph,
    llm_adapter,
    prompt_manager,
    session_manager,
    tool_executor,
)

router = APIRouter()


class ChatTurnRequest(BaseModel):
    message: str
    model: str | None = None
    chat_id: str | None = None


@router.post("/chat-turn")
async def chat_turn_alt(
    body: ChatTurnRequest,
    request: Request,
    session_mgr=Depends(session_manager),
    prompt_mgr=Depends(prompt_manager),
    llm=Depends(llm_adapter),
    context_mgr=Depends(context_manager),
    executor=Depends(tool_executor),
    audit_logger=Depends(audit_logger),
    bridge=Depends(approval_bridge),
    graph=Depends(graph),
):
    """Frontend-facing SSE endpoint. Creates session if chat_id not provided."""
    from uuid import uuid4 as _uuid4

    chat_id_str = body.chat_id
    if not chat_id_str:
        session = await session_mgr.create_session()
        chat_id_str = str(session.chat_id)
    try:
        chat_id = UUID(chat_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid chat_id")
    return await _handle_chat_turn(chat_id, body, request, session_mgr, prompt_mgr, llm, context_mgr, executor, audit_logger, bridge, graph)


async def _handle_chat_turn(
    chat_id: UUID,
    body: ChatTurnRequest,
    request: Request,
    session_mgr,
    prompt_mgr,
    llm,
    context_mgr,
    executor,
    audit_logger,
    bridge,
    graph,
):
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

    system_prompt = prompt_mgr.build_system_prompt()
    agent = Query(
        llm=llm,
        graph=graph,
        context_manager=context_mgr,
        pending_approvals=bridge,
        audit_logger=audit_logger,
        chat_id=chat_id,
    )

    available_tools = await executor.list_tools()
    messages = history + [{"role": "user", "content": body.message}]

    async def event_generator():
        collected_text: list[str] = []
        try:
            async for event in agent.run(
                messages, available_tools, system=system_prompt
            ):
                if await request.is_disconnected():
                    break
                if event.get("event") == "assistant":
                    try:
                        data = json.loads(event["data"])
                        collected_text.append(data.get("delta", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass
                yield event
        finally:
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

    return EventSourceResponse(
        event_generator(), headers={"X-Session-ID": str(chat_id)}
    )


@router.post("/sessions/{chat_id}/messages")
async def chat_turn(
    chat_id: UUID,
    body: ChatTurnRequest,
    request: Request,
    session_mgr=Depends(session_manager),
    prompt_mgr=Depends(prompt_manager),
    llm=Depends(llm_adapter),
    context_mgr=Depends(context_manager),
    executor=Depends(tool_executor),
    audit_logger=Depends(audit_logger),
    bridge=Depends(approval_bridge),
    graph=Depends(graph),
):
    return await _handle_chat_turn(
        chat_id, body, request, session_mgr, prompt_mgr, llm, context_mgr, executor, audit_logger, bridge, graph
    )
