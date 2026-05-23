from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# JSON-RPC error codes
ERROR_CODE_SECURITY_VIOLATION = 403
ERROR_CODE_EXECUTION_FAILED = 500
ERROR_CODE_TIMEOUT = 504
ERROR_CODE_TOOL_NOT_FOUND = 404
ERROR_CODE_INVALID_PARAMS = 400
@dataclass(frozen=True)
class ServerError:
    code: int
    message: str
    data: Any = None

    def to_jsonrpc(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


def security_violation(reason: str = "") -> ServerError:
    return ServerError(ERROR_CODE_SECURITY_VIOLATION, "SECURITY_VIOLATION", reason)


def execution_failed(detail: str = "") -> ServerError:
    return ServerError(ERROR_CODE_EXECUTION_FAILED, "EXECUTION_FAILED", detail)


def tool_timeout(timeout: int) -> ServerError:
    return ServerError(ERROR_CODE_TIMEOUT, "TIMEOUT", f"execution exceeded {timeout}s")


def tool_not_found(tool_name: str) -> ServerError:
    return ServerError(ERROR_CODE_TOOL_NOT_FOUND, "TOOL_NOT_FOUND", tool_name)


def invalid_params(detail: str = "") -> ServerError:
    return ServerError(ERROR_CODE_INVALID_PARAMS, "INVALID_PARAMS", detail)
