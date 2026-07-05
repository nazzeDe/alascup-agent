from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.models.tool import ApprovalStatus
from src.observability.debug_log import log as debug_log
from src.services.container import approval_bridge

router = APIRouter()


class ToolApprovalRequest(BaseModel):
    chat_id: UUID
    approval_status: str
    reason: str | None = None


@router.post("/tool-requests/{request_id}/approval")
async def approve_tool_request(
    request_id: UUID,
    body: ToolApprovalRequest,
    bridge=Depends(approval_bridge),
):
    try:
        status = ApprovalStatus(body.approval_status)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"invalid approval_status: {body.approval_status}"
        )
    if status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise HTTPException(
            status_code=400, detail=f"invalid approval_status: {body.approval_status}"
        )

    debug_log(
        "INFO",
        "Tool approval decision",
        request_id=str(request_id),
        status=status.value,
        reason=body.reason,
    )

    if not bridge:
        raise HTTPException(status_code=500, detail="approval bridge not configured")

    chat_id = bridge.get_chat_id(str(request_id))
    if not chat_id:
        raise HTTPException(
            status_code=404, detail="request not found or already handled"
        )
    if str(body.chat_id) != chat_id:
        raise HTTPException(status_code=403, detail="approval chat_id mismatch")

    completed = bridge.complete(
        str(request_id), status.value, body.reason, chat_id=chat_id
    )
    if not completed:
        raise HTTPException(
            status_code=404, detail="request not found or already handled"
        )

    return {
        "request_id": str(request_id),
        "approval_status": status.value,
        "reason": body.reason,
    }
