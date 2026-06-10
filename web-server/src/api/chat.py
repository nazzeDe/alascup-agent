from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agent.query import Query
from src.services.container import (
    approval_bridge,
    audit_logger,
    context_manager,
    error_recovery,
    graph,
    lifecycle,
    llm_adapter,
    prompt_manager,
    session_manager,
    tool_executor,
)
from src.sse_stream import SSEStream

router = APIRouter()


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
    lifecycle_dep=Depends(lifecycle),
):
    """Frontend-facing SSE endpoint. Creates session if chat_id not provided."""
    query = Query(
        llm=llm,
        graph=graph_dep,
        context_manager=context_mgr,
        session_manager=session_mgr,
        prompt_manager=prompt_mgr,
        tool_executor=executor,
        pending_approvals=bridge,
        audit_logger=audit_logger,
        error_recovery=error_rec,
        lifecycle=lifecycle_dep,
    )

    stream = SSEStream(
        user_message=body.message,
        chat_id=body.chat_id,
        session_manager=session_mgr,
        prompt_manager=prompt_mgr,
        query=query,
        tool_executor=executor,
        disconnect_check=lambda: request.is_disconnected(),
    )

    return EventSourceResponse(stream)
