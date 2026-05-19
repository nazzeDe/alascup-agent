from __future__ import annotations


class McpClientApiError(Exception):
    """mcp-client API 层使用的结构化业务异常。"""

    def __init__(self, error_code: str, message: str, status_code: int = 400, detail: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.detail = detail
