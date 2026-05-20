from pydantic import BaseModel, Field


class RuleEntry(BaseModel):
    tool_name: str
    description: str = ""


class RulesConfig(BaseModel):
    whitelist: list[RuleEntry] = Field(default_factory=list)
    blacklist: list[RuleEntry] = Field(default_factory=list)


class ServerEntry(BaseModel):
    name: str
    url: str
    transport: str = "streamable-http"


class LLMConfig(BaseModel):
    api_key: str
    api_url: str
    model: str
    summary_model: str | None = None
    fallback_model: str | None = None
    max_tokens: int = 8192


class WebServerConfig(BaseModel):
    context_window_size: int = 128000
    tool_request_timeout_seconds: int = 300
