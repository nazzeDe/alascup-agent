import json
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import interrupt
from loguru import logger

from src.agent.state import AgentState, Transition
from src.models.audit import AuditEvent, AuditLevel


async def think_node(state: AgentState, *, llm, executor=None):
    """Stream LLM, return assistant text, pending tool_calls, and streaming results.

    AG-007: Readonly tools are pre-executed inline (streaming_tool_results).
    Mutable and write tools stay in tool_calls for review_node.

    Returns:
      - messages: assistant text
      - tool_calls: non-readonly tool_use blocks (go to review → act)
      - streaming_tool_results: readonly pre-executed results
      - transition: DONE (no output) or None
    """
    available_tools = state.get("available_tools", [])
    tools = _format_tools(available_tools)
    messages = _messages(state)
    system = state.get("system")

    accumulated_text: list[str] = []
    tool_call_blocks: list[dict] = []

    async for event in llm.generate_stream(messages, tools=tools, system=system):
        if event["event"] == "assistant":
            data = json.loads(event["data"])
            accumulated_text.append(data.get("delta", ""))

        elif event["event"] == "tool_call":
            data = json.loads(event["data"])
            _merge_tool_block(tool_call_blocks, data)

        elif event["event"] == "error":
            data = json.loads(event["data"])
            return {
                "messages": [],
                "tool_calls": [],
                "llm_error": data,
                "transition": Transition.ERROR_EXIT,
            }

        elif event["event"] == "done":
            break

    result: dict = {}
    text = "".join(accumulated_text)

    if text:
        result["messages"] = [{"role": "assistant", "content": text}]

    if executor is not None and tool_call_blocks:
        pending_tool_calls, pre_executed = await _dispatch_tool_calls(
            tool_call_blocks, executor, available_tools
        )
    else:
        pending_tool_calls = tool_call_blocks
        pre_executed = []

    result["tool_calls"] = pending_tool_calls
    result["streaming_tool_results"] = pre_executed

    needs_processing = bool(pending_tool_calls or pre_executed)
    result["transition"] = Transition.DONE if not needs_processing else None
    return result


async def act_node(state: AgentState, *, executor, audit_logger=None):
    """Execute approved_tool_calls concurrently.

    Each call carries server_name, approval_status, and request_id.
    """
    tool_calls = state.get("approved_tool_calls", []) or []
    if not tool_calls:
        return {"tool_results": []}

    calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        calls.append({
            "tool_name": fn.get("name", ""),
            "arguments": _parse_args(fn.get("arguments", "{}")),
            "server_name": tc.get("server_name", ""),
            "approval_status": tc.get("approval_status", "APPROVED"),
            "request_id": tc.get("request_id", str(uuid4())),
        })

    results = await executor.execute_parallel(calls)

    formatted = []
    for i, r in enumerate(results):
        formatted.append({
            "tool_name": calls[i]["tool_name"],
            "result": r,
            "tool_call_id": str(uuid4()),
        })
        await _log_act(
            audit_logger, calls[i]["tool_name"],
            execution_status=r.get("execution_status", "UNKNOWN"),
        )

    return {"tool_results": formatted}


async def review_node(state: AgentState, *, executor, rule_engine, audit_logger):
    """Review all tool_calls: classify mutable tools → rule match → decide.

    1. Mutable tools: call executor.classify() for dynamic is_read_only/is_rollbackable
    2. Non-mutable tools: use static is_read_only from tool_call metadata
    3. rule_engine.evaluate() → REJECT / AUTO_APPROVE / NEEDS_APPROVAL
    4. Readonly/whitelist → approved_tool_calls
    5. Blacklist → rejected_tool_calls + audit
    6. High-risk → interrupt() for user approval
    """
    tool_calls = state.get("tool_calls", []) or []
    approved: list[dict] = []
    rejected: list[dict] = []
    pending: list[dict] = []

    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = _parse_args(fn.get("arguments", "{}"))

        if tc.get("mutable"):
            try:
                classification = await executor.classify(name, args, tc.get("server_name", ""))
            except Exception:
                logger.opt(exception=True).warning(
                    "classify failed for tool={tool}, treating as dangerous", tool=name,
                )
                classification = {"is_read_only": False, "is_rollbackable": False, "_classify_fallback": True}
        else:
            classification = {
                "is_read_only": tc.get("is_read_only", False),
                "is_rollbackable": tc.get("is_rollbackable", False),
            }

        is_read_only = classification.get("is_read_only", False)
        is_rollbackable = classification.get("is_rollbackable", False)

        decision = rule_engine.evaluate(name, is_read_only, is_rollbackable)

        if decision == "REJECT":
            rejected.append(tc)
            await _log_review(audit_logger, "TOOL_REJECTED", name, decision="REJECT")

        elif decision == "AUTO_APPROVE":
            tc["is_read_only"] = is_read_only
            tc["request_id"] = str(uuid4())
            approved.append(tc)
            await _log_review(audit_logger, "TOOL_AUTO_APPROVED", name, level=AuditLevel.INFO)

        else:
            tc["is_read_only"] = is_read_only
            pending.append(tc)
            await _log_review(audit_logger, "TOOL_REQUEST_CREATED", name, level=AuditLevel.WARN)

    if pending:
        request_id = str(uuid4())
        approval_result = interrupt({
            "event": "approval_required",
            "request_id": request_id,
            "pending_tool_calls": pending,
        })
        decisions = approval_result.get("decisions", [])
        for i, tc in enumerate(pending):
            user_decision = decisions[i] if i < len(decisions) else "EXPIRED"
            if user_decision == "APPROVED":
                tc["approval_status"] = "APPROVED"
                tc["request_id"] = request_id
                approved.append(tc)
                await _log_review(audit_logger, "TOOL_APPROVED",
                            tc.get("function", {}).get("name", ""),
                            level=AuditLevel.WARN)
            else:
                rejected.append(tc)
                await _log_review(audit_logger, "TOOL_REJECTED",
                            tc.get("function", {}).get("name", ""),
                            decision=user_decision)

    return {
        "approved_tool_calls": approved,
        "rejected_tool_calls": rejected,
        "transition": (
            Transition.APPROVAL_GRANTED if approved
            else Transition.APPROVAL_REJECTED
        ) if (approved or rejected) else None,
    }


async def _log_review(audit_logger, event: str, tool_name: str, level=None, **kwargs) -> None:
    if audit_logger is None:
        return
    if level is None:
        level = AuditLevel.WARN if "REJECT" in event else AuditLevel.INFO
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=level,
        actor="system",
        event=event,
        tool_name=tool_name or None,
        **{k: v for k, v in kwargs.items() if v is not None},
    ))


async def _log_act(audit_logger, tool_name: str,
             execution_status: str = "UNKNOWN") -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=AuditLevel.INFO,
        actor="system",
        event="TOOL_EXECUTED",
        tool_name=tool_name,
        execution_status=execution_status,
    ))


def observe_node(state: AgentState, *, tool_results=None):
    """追加工具执行结果到消息历史。

    合并 act_node 产出的 tool_results 和 think_node 产出的
    streaming_tool_results（AG-007），转为 role=tool 的消息。
    transition 设为 TOOL_RESULTS。

    对应 tests/README.md AG-001、AG-007。
    """
    results = list(tool_results or [])
    streaming = state.get("streaming_tool_results") or []
    results.extend(streaming)

    tool_messages: list[dict] = []

    for r in results:
        tool_messages.append({
            "role": "tool",
            "content": _format_tool_result(r),
            "tool_call_id": r.get("tool_call_id", "unknown"),
        })

    return {
        "messages": tool_messages,
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


def route_after_think(state: AgentState) -> str:
    """think 之后的路由：有 tool_call → review，仅有预执行结果 → observe，无 → END。"""
    tool_calls = state.get("tool_calls") or []
    streaming_results = state.get("streaming_tool_results") or []
    if tool_calls:
        return "review"
    if streaming_results:
        return "observe"
    return "__end__"


def _messages(state: AgentState) -> list[dict]:
    result: list[dict] = []
    for m in state.get("messages", []):
        if isinstance(m, dict):
            result.append({"role": m.get("role", ""), "content": m.get("content", "")})
        else:
            # LangGraph message object (HumanMessage, AIMessage, ToolMessage...)
            role = getattr(m, "type", "unknown")
            content = getattr(m, "content", "")
            result.append({"role": role, "content": content})
    return result


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
        full_name = f"{server}/{name}" if server else name
        result.append({
            "type": "function",
            "function": {"name": full_name, "description": desc, "parameters": params},
        })
    return result


async def _dispatch_tool_calls(
    tool_call_blocks: list[dict], executor, available_tools: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Parse server_name prefix, attach metadata, pre-execute readonly tools.

    Dispatch based on static tool metadata:
    - Mutable tools → pending (dynamic classify_tool called later in review_node)
    - Non-mutable readonly → pre-executed via executor.execute_parallel
    - Non-mutable write → pending (needs approval in review_node)

    Returns (pending_tool_calls, pre_executed_results).
    """
    # Build lookup keyed by "server_name/tool_name" and bare name
    tool_index: dict[str, dict] = {}
    for t in available_tools:
        server = t.get("server_name", "")
        name = t.get("name", "")
        if server and name:
            tool_index[f"{server}/{name}"] = t
            tool_index[name] = t  # fallback for tools without prefix

    pending: list[dict] = []
    dispatch: list[dict] = []
    for tc in tool_call_blocks:
        fn = tc.get("function", {})
        full_name = fn.get("name", "")
        args = _parse_args(fn.get("arguments", "{}"))

        # Parse "server_name/tool_name" prefix (Q24)
        if "/" in full_name:
            server_name, tool_name = full_name.split("/", 1)
        else:
            server_name = ""
            tool_name = full_name

        tc["server_name"] = server_name
        tc["function"]["name"] = tool_name  # restore bare name for MCP call

        meta = tool_index.get(full_name, {})
        is_mutable = meta.get("mutable", False)
        is_read_only = meta.get("is_read_only", False)

        if is_mutable:
            tc["mutable"] = True
            tc["is_read_only"] = None  # determined by classify_tool
            pending.append(tc)
        elif is_read_only:
            dispatch.append({
                "tool_name": tool_name,
                "arguments": args,
                "server_name": server_name,
                "approval_status": "APPROVED",
                "request_id": str(uuid4()),
            })
        else:
            tc["mutable"] = False
            tc["is_read_only"] = False
            pending.append(tc)

    pre_executed: list[dict] = []
    if dispatch:
        disp_results = await executor.execute_parallel(dispatch)
        for i, dr in enumerate(disp_results):
            pre_executed.append({
                "tool_name": dispatch[i]["tool_name"],
                "result": dr,
                "tool_call_id": str(uuid4()),
            })

    return pending, pre_executed


def _parse_args(args: str) -> dict:
    try:
        return json.loads(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        return {}


def _merge_tool_block(blocks: list[dict], chunk: dict) -> None:
    """将流式 tool_call chunk 合并到 tool_call_blocks 列表。

    OpenAI 流式格式中，同一个 tool_use 会分多个 chunk 到达：
      - 第一个 chunk 带 name
      - 后续 chunk 带 arguments 增量
    """
    fn = chunk.get("function", {})
    name = fn.get("name", "")
    args_chunk = fn.get("arguments", "")

    if name:
        blocks.append({"function": {"name": name, "arguments": args_chunk}})
    elif args_chunk and blocks:
        blocks[-1]["function"]["arguments"] += args_chunk
    elif args_chunk:
        blocks.append({"function": {"name": "", "arguments": args_chunk}})
