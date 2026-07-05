"""Protocol mappers for agent domain objects."""

import json
from typing import Any
from uuid import UUID

from src.agent.domain import AgentMessage, AgentToolCall, AgentToolResult, ToolFunction
from src.agent.shared import parse_json_object


def message_from_wire(raw: dict | AgentMessage) -> AgentMessage:
    if isinstance(raw, AgentMessage):
        return raw
    role = raw.get("role", raw.get("type", "user"))
    if role in ("human",):
        role = "user"
    elif role in ("ai", "tool_call"):
        role = "assistant"
    elif role in ("tool_result",):
        role = "tool"
    tool_calls = [tool_call_from_openai(tc) for tc in raw.get("tool_calls", []) or []]
    return AgentMessage(
        role=role,
        content=str(raw.get("content", "")),
        tool_calls=tool_calls,
        tool_call_id=raw.get("tool_call_id"),
        name=raw.get("name"),
        reasoning_content=raw.get("reasoning_content"),
    )


def messages_from_wire(messages: list[dict | AgentMessage]) -> list[AgentMessage]:
    return [message_from_wire(m) for m in messages]


def message_to_openai(message: AgentMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "assistant":
        if message.tool_calls:
            result["tool_calls"] = [
                tool_call_to_openai(tc) for tc in message.tool_calls
            ]
        if message.reasoning_content:
            result["reasoning_content"] = message.reasoning_content
    if message.role == "tool":
        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id
        if message.name:
            result["name"] = message.name
    return result


def messages_to_openai(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    return [message_to_openai(m) for m in messages]


def message_to_db_wire(message: AgentMessage) -> dict[str, Any]:
    result = message_to_openai(message)
    if message.tool_calls:
        result["tool_calls"] = [tool_call_to_openai(tc) for tc in message.tool_calls]
    return result


def tool_call_from_openai(raw: dict | AgentToolCall) -> AgentToolCall:
    if isinstance(raw, AgentToolCall):
        return raw
    fn = raw.get("function", {}) or {}
    full_name = fn.get("name", "")
    arguments = fn.get("arguments", {})
    data = dict(
        function=ToolFunction(
            name=full_name,
            arguments=parse_json_object(arguments)
            if isinstance(arguments, str)
            else (arguments or {}),
        ),
        server_name=raw.get("server_name", ""),
        mutable=bool(raw.get("mutable", False)),
        is_read_only=raw.get("is_read_only"),
        is_rollbackable=bool(raw.get("is_rollbackable", False)),
        approval_status=raw.get("approval_status"),
        request_id=raw.get("request_id"),
        call_id=_coerce_uuid(raw.get("call_id")),
        rejection_reason=raw.get("rejection_reason"),
    )
    if raw.get("id"):
        data["id"] = raw["id"]
    return AgentToolCall(**data)


def tool_call_to_openai(call: AgentToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.function.name,
            "arguments": json.dumps(call.function.arguments),
        },
    }


def tool_call_to_dispatch(
    call: AgentToolCall, chat_id: str | None = None
) -> dict[str, Any]:
    return {
        "tool_name": call.function.name,
        "arguments": call.function.arguments,
        "server_name": call.server_name,
        "approval_status": call.approval_status or "APPROVED",
        "request_id": call.request_id or "",
        "chat_id": chat_id or "",
    }


def tool_result_from_wire(raw: dict | AgentToolResult) -> AgentToolResult:
    if isinstance(raw, AgentToolResult):
        return raw
    return AgentToolResult(
        tool_name=raw.get("tool_name", ""),
        tool_call_id=raw.get("tool_call_id", ""),
        result=raw.get("result", {}),
        is_read_only=bool(raw.get("is_read_only", False)),
        is_rollbackable=bool(raw.get("is_rollbackable", False)),
        server_name=raw.get("server_name", ""),
        call_id=_coerce_uuid(raw.get("call_id")),
    )


def tool_result_to_wire(result: AgentToolResult) -> dict[str, Any]:
    data = result.model_dump(mode="python", exclude_none=True)
    if result.call_id is not None:
        data["call_id"] = str(result.call_id)
    return data


def tool_message_from_result(result: AgentToolResult, content: str) -> AgentMessage:
    return AgentMessage(
        role="tool",
        content=content,
        tool_call_id=result.tool_call_id,
        name=result.tool_name,
    )


def _coerce_uuid(value) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
