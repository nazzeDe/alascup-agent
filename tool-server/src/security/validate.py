import hmac
import uuid

from loguru import logger


def validate_execution(
    approval_status: str,
    request_id: str,
    is_read_only: bool,
    auth_token: str = "",
    shared_secret: str = "",
) -> tuple[bool, str]:
    if shared_secret and not hmac.compare_digest(auth_token, shared_secret):
        logger.warning("execution_rejected reason=invalid_auth_token")
        return False, "invalid tool-server auth token"
    if approval_status != "APPROVED":
        logger.warning(
            "execution_rejected reason=not_approved got={s}", s=approval_status
        )
        return False, f"approval_status must be APPROVED, got {approval_status}"
    if not is_read_only:
        if not request_id:
            logger.warning("execution_rejected reason=missing_request_id")
            return False, "request_id required for destructive operations"
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError):
            logger.warning(
                "execution_rejected reason=invalid_request_id value={v!r}", v=request_id
            )
            return False, f"invalid request_id UUID: {request_id!r}"
    return True, ""
