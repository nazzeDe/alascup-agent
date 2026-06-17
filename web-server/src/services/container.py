"""Typed services container and shared FastAPI dependencies.

Replaces the untyped dict[str, Any] with a dataclass so service
access is IDE-completable and typo-proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request

from src.agent.loop.runner import AgentLoop
from src.mcp_client.executor import ToolExecutor
from src.models.message import Message
from src.models.session import ChatSession
from src.models.tool import ToolCall
from src.security.pending import ApprovalBridge
from src.security.rule_engine import RuleEngine
from src.services.context_manager import ContextManager
from src.services.error_recovery import ErrorRecovery
from src.services.llm_adapter import LLMAdapter
from src.services.prompt_manager import PromptManager


class SessionManager(Protocol):
    """Structural interface shared by InMemorySessionManager and PostgresSessionManager."""

    async def create_session(self) -> ChatSession: ...
    async def get_session(self, chat_id: UUID) -> ChatSession: ...
    async def list_sessions(self) -> list[ChatSession]: ...
    async def delete_session(self, chat_id: UUID) -> None: ...
    async def add_message(self, chat_id: UUID, msg: Message) -> None: ...
    async def add_tool_call(self, chat_id: UUID, call: ToolCall) -> UUID: ...
    async def update_tool_call(self, tool_call_id: UUID, chat_id: UUID, *, approval_status: Any | None = None, execution_status: Any | None = None, error: dict | None = None, backup_ref: str | None = None, llm_trace_id: UUID | None = None) -> None: ...


class AuditLogger(Protocol):
    """Structural interface shared by InMemoryAuditLogger and PostgresAuditLogger."""

    async def log(self, event: Any) -> None: ...


@dataclass
class Services:
    llm_adapter: LLMAdapter
    session_manager: SessionManager
    prompt_manager: PromptManager
    context_manager: ContextManager
    rule_engine: RuleEngine
    tool_executor: ToolExecutor
    audit_logger: AuditLogger
    approval_bridge: ApprovalBridge
    graph: Any = None  # Deprecated, replaced by AgentLoop; kept for backward compat
    agent_loop: AgentLoop | None = None
    error_recovery: ErrorRecovery | None = None
    agent_max_iterations: int = 30
    agent_token_ceiling_ratio: float = 0.95
    db: Any | None = None
    lifecycle: Any | None = None  # ToolCallLifecycle


def _services(request: Request) -> Services:
    return request.app.state.services


# ── typed FastAPI dependencies ──────────────────────────────────────────


async def llm_adapter(request: Request) -> LLMAdapter:
    return _services(request).llm_adapter


async def session_manager(request: Request) -> SessionManager:
    return _services(request).session_manager


async def prompt_manager(request: Request) -> PromptManager:
    return _services(request).prompt_manager


async def context_manager(request: Request) -> ContextManager:
    return _services(request).context_manager


async def rule_engine(request: Request) -> RuleEngine:
    return _services(request).rule_engine


async def tool_executor(request: Request) -> ToolExecutor:
    return _services(request).tool_executor


async def audit_logger(request: Request) -> AuditLogger:
    return _services(request).audit_logger


async def approval_bridge(request: Request) -> ApprovalBridge:
    return _services(request).approval_bridge


async def error_recovery(request: Request) -> ErrorRecovery | None:
    return _services(request).error_recovery


async def agent_loop(request: Request):
    return _services(request).agent_loop


# Deprecated — kept for backward compat
async def graph(request: Request):
    return _services(request).graph


async def db(request: Request):
    return _services(request).db


async def lifecycle(request: Request):
    return _services(request).lifecycle


async def agent_max_iterations(request: Request) -> int:
    return _services(request).agent_max_iterations


async def agent_token_ceiling_ratio(request: Request) -> float:
    return _services(request).agent_token_ceiling_ratio
