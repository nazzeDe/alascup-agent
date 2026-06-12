from src.agent.results import ObserveOutput
from src.agent.state import Transition


def observe_node(state, *, tool_results=None):
    """Append tool execution results and rejected tools to message history.

    Merges tool_results from act_node, streaming_tool_results from think_node
    (AG-007), and rejected_tool_calls from human/rule rejection — converting
    them into role=tool messages so the LLM can adjust its approach.
    """
    results = list(tool_results or [])
    state_tr = state.get("tool_results") or []
    results.extend(state_tr)
    streaming = state.get("streaming_tool_results") or []
    results.extend(streaming)

    rejected = state.get("rejected_tool_calls") or []
    for tc in rejected:
        fn = tc.get("function", {})
        results.append({
            "tool_name": fn.get("name", "unknown"),
            "tool_call_id": tc.get("id", "rejected"),
            "result": {"execution_status": "REJECTED", "error": {"message": "Tool was rejected by human or policy. Do NOT retry this exact tool call — propose an alternative approach."}},
        })

    tool_messages: list[dict] = []

    for r in results:
        tool_messages.append({
            "role": "tool",
            "content": _format_tool_result(r),
            "tool_call_id": r.get("tool_call_id", "unknown"),
            "name": r.get("tool_name", "unknown"),
        })

    return ObserveOutput(
        tool_messages=tool_messages,
        emitted_results=results,
        transition=Transition.TOOL_RESULTS,
    )


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


def route_after_review(state) -> str:
    """Route after review: pending_approval → END, otherwise → act."""
    if state.get("pending_approval"):
        return "__end__"
    return "act"


def route_after_think(state) -> str:
    """Route after think: approved → act, tool_call → review, streaming → observe, none → END."""
    approved = state.get("approved_tool_calls") or []
    if approved:
        return "act"
    tool_calls = state.get("tool_calls") or []
    streaming_results = state.get("streaming_tool_results") or []
    if tool_calls:
        return "review"
    if streaming_results:
        return "observe"
    return "__end__"
