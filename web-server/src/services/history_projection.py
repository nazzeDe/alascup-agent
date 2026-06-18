"""Conversation history projection for LLM-ready messages."""

from datetime import datetime

from src.models.message import MessageType
from src.models.session import ChatSession


def build_llm_history(session: ChatSession) -> list[dict]:
    """Project stored session data into LLM-ready message history."""
    entries: list[tuple[datetime, str, object]] = []

    for m in session.messages:
        ts = datetime.fromisoformat(m.timestamp)
        entries.append((ts, "msg", m))

    entries.sort(key=lambda e: e[0])

    history: list[dict] = []
    for _ts, kind, obj in entries:
        history.append(_message_to_history(obj))

    return history


def _message_to_history(m) -> dict:
    role = m.type.value if isinstance(m.type, MessageType) else m.type
    entry: dict = {"role": role, "content": m.content}
    if m.tool_calls:
        entry["tool_calls"] = m.tool_calls
    if m.type == MessageType.TOOL_RESULT:
        entry["role"] = "tool"
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.tool_name:
            entry["name"] = m.tool_name
    if m.reasoning_content:
        entry["reasoning_content"] = m.reasoning_content
    return entry
