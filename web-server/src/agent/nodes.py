import json
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import interrupt
from loguru import logger

from src.agent.state import AgentState, Transition
from src.models.audit import AuditEvent, AuditLevel


async def think_node(state: AgentState, *, llm, executor=None, classifier=None):
    """流式调用 LLM，返回 assistant 文本、tool_calls 列表和流式执行结果。

    AG-007: 流结束后立即分类并执行只读 tool_call，结果放入
    streaming_tool_results 供 observe_node 直接消费，跳过 review/act。

    返回:
      - messages: assistant 文本消息
      - tool_calls: 非只读 tool_use block（需走 review → act）
      - streaming_tool_results: 只读工具预执行结果
      - transition: DONE（无产出时）或 None
    """
    tools = _format_tools(state.get("available_tools", []))
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

    # AG-007: 分类 tool_call blocks——只读的立即执行，其余保留走审查
    if executor is not None and classifier is not None and tool_call_blocks:
        pending_tool_calls, pre_executed = await _classify_and_dispatch(
            tool_call_blocks, executor, classifier
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
    """执行 approved_tool_calls。

    并发执行所有已审批工具（只读并行，高风险由 review_node 保证串行）。

    对应 tests/README.md AG-001、AG-003、AL-001。
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
            "is_read_only": tc.get("is_read_only", True),
            "approval_status": tc.get("approval_status"),
        })

    results = await executor.execute_parallel(calls)

    formatted = []
    for i, r in enumerate(results):
        formatted.append({
            "tool_name": calls[i]["tool_name"],
            "result": r,
            "tool_call_id": str(uuid4()),
        })
        await _log_act(audit_logger, calls[i]["tool_name"],
                       is_read_only=calls[i]["is_read_only"],
                       execution_status=r.get("execution_status", "UNKNOWN"))

    return {"tool_results": formatted}


async def review_node(state: AgentState, *, classifier, rule_engine, audit_logger):
    """审查所有 tool_call：分级 → 规则匹配 → 决策。

    1. 调 classifier.classify 获取 is_read_only / is_rollbackable
    2. 调 rule_engine.evaluate 匹配规则（黑名单/白名单/按分级）
    3. 只读/白名单 → 加入 approved_tool_calls
    4. 黑名单 → 加入 rejected_tool_calls，写审计日志
    5. 高风险 → 待审批列表，如有则调用 interrupt() 暂停
    6. interrupt 恢复后，处理用户审批结果
    """
    tool_calls = state.get("tool_calls", []) or []
    approved: list[dict] = []
    rejected: list[dict] = []
    pending: list[dict] = []

    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = _parse_args(fn.get("arguments", "{}"))

        classification = await classifier.classify(name, args)
        is_read_only = classification.get("is_read_only", True)
        is_rollbackable = classification.get("is_rollbackable", False)

        decision = rule_engine.evaluate(name, is_read_only, is_rollbackable)

        if decision == "REJECT":
            rejected.append(tc)
            await _log_review(audit_logger, "TOOL_REJECTED", name, decision="REJECT")

        elif decision == "AUTO_APPROVE":
            tc["is_read_only"] = is_read_only
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


async def _log_act(audit_logger, tool_name: str, is_read_only: bool = True,
             execution_status: str = "UNKNOWN") -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=AuditLevel.INFO if is_read_only else AuditLevel.WARN,
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
            desc = t.get("description", "")
            params = t.get("params_schema", {})
        else:
            name = getattr(t, "name", "")
            desc = getattr(t, "description", "")
            params = getattr(t, "params_schema", {})
        result.append({
            "type": "function",
            "function": {"name": name, "description": desc, "parameters": params},
        })
    return result


async def _classify_and_dispatch(
    tool_call_blocks: list[dict], executor, classifier
) -> tuple[list[dict], list[dict]]:
    """Classify tool calls and pre-execute readonly ones.

    Returns (pending_tool_calls, pre_executed_results).
    """
    exec_calls = []
    for tc in tool_call_blocks:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = _parse_args(fn.get("arguments", "{}"))
        exec_calls.append((tc, name, args))

    classifications = []
    for _, name, args in exec_calls:
        try:
            c = await classifier.classify(name, args)
        except Exception:
            logger.opt(exception=True).warning(
                "classify failed for tool={tool}, falling back to readonly",
                tool=name,
            )
            c = {"is_read_only": True, "is_rollbackable": False, "_classify_fallback": True}
        classifications.append(c)

    pending: list[dict] = []
    dispatch: list[dict] = []
    for i, (tc, name, args) in enumerate(exec_calls):
        is_read_only = classifications[i].get("is_read_only", True)
        if is_read_only:
            dispatch.append({
                "tool_name": name,
                "arguments": args,
                "is_read_only": True,
            })
        else:
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
