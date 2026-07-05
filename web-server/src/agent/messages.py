"""Single message normalization — the only place that knows about message objects.

After history reconstruction or node execution, messages may be a mix of plain
dicts and message objects (AIMessage, HumanMessage, ToolMessage, ...).
normalize_message() converts any message to a plain dict with a stable
schema. All downstream consumers can then assume messages are dicts.
"""

import json

from src.agent.state import ROLE_MAP


def normalize_message(m: dict | object) -> dict:
    """Convert any message (dict or LangChain object) to a plain dict.

    Stable schema: {role, content, id?, tool_calls?, tool_call_id?, name?, reasoning_content?}
    """
    if isinstance(m, dict):
        return _from_dict(m)
    return _from_object(m)


def _from_dict(m: dict) -> dict:
    result = {
        "role": ROLE_MAP.get(m.get("role", m.get("type", "")), m.get("role", "")),
        "content": str(m.get("content", "")),
    }
    if "id" in m:
        result["id"] = m["id"]
    if "tool_calls" in m:
        result["tool_calls"] = [_tc_normalize(tc) for tc in m["tool_calls"]]
    if "tool_call_id" in m:
        result["tool_call_id"] = m["tool_call_id"]
    if "name" in m:
        result["name"] = m["name"]
    if "reasoning_content" in m:
        result["reasoning_content"] = m["reasoning_content"]
    return result


def _from_object(m) -> dict:
    # Handle LangChain message objects (AIMessage, HumanMessage, ToolMessage, ...).
    raw_role = getattr(m, "type", getattr(m, "role", ""))
    result = {
        "role": ROLE_MAP.get(raw_role, raw_role),
        "content": str(getattr(m, "content", "")),
    }
    mid = getattr(m, "id", None)
    if mid:
        result["id"] = str(mid)
    tcs = getattr(m, "tool_calls", None)
    if tcs:
        result["tool_calls"] = [_tc_normalize(tc) for tc in tcs]
    tc_id = getattr(m, "tool_call_id", None)
    if tc_id:
        result["tool_call_id"] = tc_id
    name = getattr(m, "name", None)
    if name:
        result["name"] = name
    additional_kwargs = getattr(m, "additional_kwargs", None) or {}
    rc = additional_kwargs.get("reasoning_content", "")
    if rc:
        result["reasoning_content"] = rc
    return result


def _tc_normalize(tc) -> dict:
    """Convert a tool call (dict or object) to OpenAI-compatible dict format.

    This mirrors the original _langchain_tc_to_openai logic.
    Object tool calls get converted to dicts first, then normalized.
    """
    if not isinstance(tc, dict):
        args = getattr(tc, "args", {})
        if not isinstance(args, str):
            args = json.dumps(args)
        return {
            "id": getattr(tc, "id", ""),
            "type": "function",
            "function": {"name": getattr(tc, "name", ""), "arguments": args},
        }
    # Already OpenAI format: {id, function: {name, arguments}, type: "function"}
    if tc.get("function") is not None:
        return {"id": tc.get("id", ""), "function": tc["function"], "type": "function"}
    # Object-derived dict format: {name, args, id, type: "tool_call"}
    args = tc.get("args", {})
    if not isinstance(args, str):
        args = json.dumps(args)
    return {
        "id": tc.get("id", ""),
        "type": "function",
        "function": {"name": tc.get("name", ""), "arguments": args},
    }
