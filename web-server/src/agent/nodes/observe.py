from src.agent.state import Transition


def observe_node(state, *, tool_results=None):
    """Append tool execution results to message history.

    Merges tool_results from act_node and streaming_tool_results from think_node
    (AG-007), converting them into role=tool messages.
    """
    results = list(tool_results or [])
    state_tr = state.get("tool_results") or []
    results.extend(state_tr)
    streaming = state.get("streaming_tool_results") or []
    results.extend(streaming)

    tool_messages: list[dict] = []

    for r in results:
        tool_messages.append({
            "role": "tool",
            "content": _format_tool_result(r),
            "tool_call_id": r.get("tool_call_id", "unknown"),
            "name": r.get("tool_name", "unknown"),
        })

    return {
        "messages": tool_messages,
        "streaming_tool_results": [],
        "tool_results": [],
        "_emitted_results": results,
        "transition": Transition.TOOL_RESULTS,
    }


def _format_tool_result(r: dict) -> str:
    name = r.get("tool_name", "unknown")
    result = r.get("result", {})
    status = result.get("execution_status", "UNKNOWN")
    output = result.get("output", "")
    error = result.get("error", {})
    if isinstance(error, dict):
        error_msg = error.get("message", "")
    else:
        error_msg = str(error) if error else ""

    parts = [f"[{name}] execution_status={status}"]
    if output:
        parts.append(f"output={output}")
    if error_msg:
        parts.append(f"error={error_msg}")

    return "\n".join(parts)


def route_after_think(state) -> str:
    """Route after think: has tool_call → review, only streaming results → observe, none → END."""
    tool_calls = state.get("tool_calls") or []
    streaming_results = state.get("streaming_tool_results") or []
    if tool_calls:
        return "review"
    if streaming_results:
        return "observe"
    return "__end__"
