"""Typed services container and shared FastAPI dependencies.

Replaces the untyped dict[str, Any] with a dataclass so service
access is IDE-completable and typo-proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request

from src.models.message import Message
from src.models.session import ChatSession
from src.models.tool import ToolCall


class SessionManager(Protocol):
    """Structural interface shared by InMemorySessionManager and PostgresSessionManager."""

    async def create_session(self) -> ChatSession: ...
    async def get_session(self, chat_id: UUID) -> ChatSession: ...
    async def list_sessions(self) -> list[ChatSession]: ...
    async def add_message(self, chat_id: UUID, msg: Message) -> None: ...
    async def add_tool_call(self, chat_id: UUID, call: ToolCall) -> None: ...


class AuditLogger(Protocol):
    """Structural interface shared by InMemoryAuditLogger and PostgresAuditLogger."""

    async def log(self, event: Any) -> None: ...


@dataclass
class Services:
    llm_adapter: Any
    session_manager: SessionManager
    prompt_manager: Any
    context_manager: Any
    rule_engine: Any
    tool_executor: Any
    audit_logger: AuditLogger
    approval_bridge: Any
    checkpointer: Any
    graph: Any
    db: Any | None = None


def _services(request: Request) -> Services:
    return request.app.state.services


# ── typed FastAPI dependencies ──────────────────────────────────────────


def llm_adapter(request: Request):
    return _services(request).llm_adapter


def session_manager(request: Request) -> SessionManager:
    return _services(request).session_manager


def prompt_manager(request: Request):
    return _services(request).prompt_manager


def context_manager(request: Request):
    return _services(request).context_manager


def rule_engine(request: Request):
    return _services(request).rule_engine


def tool_executor(request: Request):
    return _services(request).tool_executor


def audit_logger(request: Request) -> AuditLogger:
    return _services(request).audit_logger


def approval_bridge(request: Request):
    return _services(request).approval_bridge


def checkpointer(request: Request):
    return _services(request).checkpointer


def graph(request: Request):
    return _services(request).graph
