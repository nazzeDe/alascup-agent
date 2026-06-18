from src.agent.results import ObserveOutput
from src.agent.shared import error_message
from src.agent.state import Transition
from src.agent.domain import AgentToolResult
from src.agent.mappers import tool_message_from_result, tool_result_from_wire


def observe_node(state, *, tool_results=None):
    """Append tool execution results and rejected tools to message history.

    Merges tool_results from act_node, streaming_tool_results from think_node
    (AG-007), and rejected_tool_calls from human/rule rejection — converting
    them into role=tool messages so the LLM can adjust its approach.
    """
    results = [tool_result_from_wire(r) for r in (tool_results or [])]
    results.extend(state.tool_results)
    results.extend(state.streaming_tool_results)

    for tc in state.rejected_tool_calls:
        result = {
            "execution_status": "REJECTED",
            "error": {"message": "Tool was rejected by human. Do NOT retry."},
        }
        reason = tc.rejection_reason
        if reason:
            result["rejection_reason"] = reason
        results.append(AgentToolResult(
            tool_name=tc.function.name or "unknown",
            tool_call_id=tc.id or "rejected",
            result=result,
        ))

    tool_messages = []

    for r in results:
        tool_messages.append(tool_message_from_result(r, _format_tool_result(r)))

    return ObserveOutput(
        tool_messages=tool_messages,
        emitted_results=results,
        transition=Transition.TOOL_RESULTS,
    )


def _format_tool_result(r: AgentToolResult) -> str:
    name = r.tool_name or "unknown"
    result = r.result
    status = result.get("execution_status", "UNKNOWN")
    output = result.get("output", "")
    rejection_reason = result.get("rejection_reason", "")
    error_msg = error_message(result.get("error", {}))

    parts = [f"[{name}] execution_status={status}"]
    if output:
        parts.append(f"output={output}")
    if rejection_reason:
        parts.append(f"rejection_reason={rejection_reason}")
    if error_msg:
        parts.append(f"error={error_msg}")

    return "\n".join(parts)


def route_after_review(state) -> str:
    """Route after review: pending_approval → END, otherwise → act."""
    if state.pending_approval:
        return "__end__"
    return "act"


def route_after_think(state) -> str:
    """Route after think: approved → act, tool_call → review, streaming → observe, none → END."""
    if state.approved_tool_calls:
        return "act"
    if state.tool_calls:
        return "review"
    if state.streaming_tool_results:
        return "observe"
    return "__end__"
