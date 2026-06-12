from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agent.loop.orchestrator import LoopOrchestrator
from src.chat_turn import ChatTurn
from src.services.container import (
    agent_max_iterations,
    agent_token_ceiling_ratio,
    approval_bridge,
    audit_logger,
    context_manager,
    error_recovery,
    lifecycle,
    llm_adapter,
    prompt_manager,
    rule_engine as rule_engine_dep,
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
    error_rec=Depends(error_recovery),
    lifecycle_dep=Depends(lifecycle),
    max_iter=Depends(agent_max_iterations),
    token_ratio=Depends(agent_token_ceiling_ratio),
    rules=Depends(rule_engine_dep),
):
    """Frontend-facing SSE endpoint. Creates session if chat_id not provided."""

    def orchestrator_builder():
        return LoopOrchestrator(
            context_manager=context_mgr,
            bridge=bridge,
            audit_logger=audit_logger,
            error_recovery=error_rec,
            llm=llm,
            chat_id=body.chat_id or "",
            lifecycle=lifecycle_dep,
            agent_max_iterations=max_iter,
            agent_token_ceiling_ratio=token_ratio,
            tool_executor=executor,
            rule_engine=rules,
        )

    turn = ChatTurn(
        user_message=body.message,
        chat_id=body.chat_id,
        session_manager=session_mgr,
        prompt_manager=prompt_mgr,
        orchestrator_builder=orchestrator_builder,
        tool_executor=executor,
        disconnect_check=lambda: request.is_disconnected(),
    )

    stream = SSEStream(turn=turn, disconnect_check=lambda: request.is_disconnected())

    return EventSourceResponse(stream)
