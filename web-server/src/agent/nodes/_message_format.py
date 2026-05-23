import json

_ROLE_MAP = {"human": "user", "ai": "assistant"}


def _messages(state) -> list[dict]:
    """Convert LangGraph messages to OpenAI-compatible dicts."""
    result: list[dict] = []
    for m in state.get("messages", []):
        role, content, meta = _normalize_message(m)
        entry: dict = {"role": role, "content": content}
        if role == "assistant":
            tcs = meta.get("tool_calls")
            if tcs:
                entry["tool_calls"] = tcs
            rc = meta.get("reasoning_content", "")
            if rc:
                entry["reasoning_content"] = rc
        elif role == "tool":
            tc_id = meta.get("tool_call_id", "")
            if tc_id:
                entry["tool_call_id"] = tc_id
            nm = meta.get("name", "")
            if nm:
                entry["name"] = nm
        result.append(entry)
    return result


def _normalize_message(m: dict | object) -> tuple[str, str, dict]:
    """Normalize a dict or LangGraph message object into (role, content, meta)."""
    is_dict = isinstance(m, dict)

    def _get(key: str, attr: str, default=""):
        return m.get(key, default) if is_dict else str(getattr(m, attr, default))

    role = _get("role", "type", "unknown")
    role = _ROLE_MAP.get(role, role)
    content = _get("content", "content", "")

    meta: dict = {}
    if is_dict:
        tcs = m.get("tool_calls")
        if tcs:
            meta["tool_calls"] = tcs
        rc = m.get("reasoning_content", "")
        if rc:
            meta["reasoning_content"] = rc
        tc_id = m.get("tool_call_id", "")
        if tc_id:
            meta["tool_call_id"] = tc_id
        nm = m.get("name", "")
        if nm:
            meta["name"] = nm
    else:
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            meta["tool_calls"] = [_langchain_tc_to_openai(tc) for tc in tcs]
        rc = getattr(m, "additional_kwargs", {}).get("reasoning_content", "")
        if rc:
            meta["reasoning_content"] = rc
        tc_id = getattr(m, "tool_call_id", "")
        if tc_id:
            meta["tool_call_id"] = tc_id
        nm = getattr(m, "name", "")
        if nm:
            meta["name"] = nm

    return role, content, meta


def _langchain_tc_to_openai(tc: dict) -> dict:
    fn = tc.get("function")
    if fn is not None:
        return {"id": tc.get("id", ""), "function": fn, "type": "function"}
    args = tc.get("args", {})
    if not isinstance(args, str):
        args = json.dumps(args)
    return {
        "id": tc.get("id", ""),
        "type": "function",
        "function": {"name": tc.get("name", ""), "arguments": args},
    }


def _format_tools(tools: list) -> list[dict]:
    result: list[dict] = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name", "")
            server = t.get("server_name", "")
            desc = t.get("description", "")
            params = t.get("params_schema", {})
        else:
            name = getattr(t, "name", "")
            server = getattr(t, "server_name", "")
            desc = getattr(t, "description", "")
            params = getattr(t, "params_schema", {})
        full_name = f"{server}__{name}" if server else name
        if not params or not isinstance(params, dict) or params.get("type") != "object":
            params = {"type": "object", "properties": {}}
        result.append({
            "type": "function",
            "function": {"name": full_name, "description": desc, "parameters": params},
        })
    return result
