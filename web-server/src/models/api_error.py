from pydantic import BaseModel


class ApiErrorResponse(BaseModel):
    code: str
    message: str


class WebServerError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
