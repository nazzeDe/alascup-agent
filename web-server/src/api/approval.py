import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.agent.query import Query
from src.models.message import Message, MessageType
from src.models.tool import ApprovalStatus
from src.services.container import (
    approval_bridge,
    audit_logger,
    context_manager,
    error_recovery,
    graph,
    llm_adapter,
    session_manager,
)

router = APIRouter()


class ToolApprovalRequest(BaseModel):
    approval_status: str
    reason: str | None = None


@router.post("/tool-requests/{request_id}/approval")
async def approve_tool_request(
    request_id: UUID,
    body: ToolApprovalRequest,
    bridge=Depends(approval_bridge),
    session_mgr=Depends(session_manager),
    llm=Depends(llm_adapter),
    context_mgr=Depends(context_manager),
    audit_logger=Depends(audit_logger),
    graph=Depends(graph),
    error_rec=Depends(error_recovery),
):
    try:
        status = ApprovalStatus(body.approval_status)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"invalid approval_status: {body.approval_status}"
        )

    if not bridge:
        raise HTTPException(status_code=500, detail="approval bridge not configured")

    chat_id_str = bridge.get_chat_id(str(request_id))
    if not chat_id_str:
        raise HTTPException(status_code=404, detail="request not found")

    chat_id = UUID(chat_id_str)
    agent = Query(
        llm=llm,
        graph=graph,
        context_manager=context_mgr,
        audit_logger=audit_logger,
        error_recovery=error_rec,
        chat_id=chat_id,
    )

    # Loop until the agent finishes — each resume may hit another interrupt
    # if the LLM requests more tools after seeing results.
    collected_text: list[str] = []
    pending_decisions: list[str] = [status.value]

    while True:
        saw_approval = False
        async for event in agent.resume(pending_decisions):
            if event.get("event") == "assistant":
                try:
                    data = json.loads(event["data"])
                    collected_text.append(data.get("delta", ""))
                except (json.JSONDecodeError, KeyError):
                    pass
            elif event.get("event") == "tool_approval_required":
                saw_approval = True
                pending_decisions = ["APPROVED"]
                break

        if not saw_approval:
            break
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

    return {
        "approval_status": status.value,
        "reason": body.reason,
        "agent_resumed": True,
    }
