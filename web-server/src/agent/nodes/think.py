import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger

from src.agent.nodes import _event_queue, _chat_id_ctx, _session_manager_ctx
from src.agent.nodes._message_format import _format_tools, _messages
from src.agent.nodes._tool_dispatch import _dispatch_tool_calls
from src.agent.state import Transition
from src.models.tool import ApprovalStatus, ExecutionStatus, ServerName, ToolCall
from src.observability.debug_log import log as debug_log
from src.tools import start_feature, complete_feature


async def think_node(state, *, llm, executor=None):
    """Stream LLM, return assistant text, pending tool_calls, and streaming results.

    AG-007: Readonly tools are pre-executed inline (streaming_tool_results).
    Mutable and write tools stay in tool_calls for review_node.

    Fast-path: when approved_tool_calls is already populated (orchestrator
    merged human approval decisions), skip LLM and route directly to act_node.
    """
    # Fast-path: orchestrator has already populated approved_tool_calls
    # after human approval. Skip LLM, let route_after_think send us to act.
    if state.get("approved_tool_calls"):
        return {
            "tool_calls": [],
            "streaming_tool_results": [],
            "transition": None,
        }

    available_tools = state.get("available_tools", [])
    tools = _format_tools(available_tools)
    messages = _messages(state)
    system = state.get("system")

    accumulated_text: list[str] = []
    accumulated_reasoning: list[str] = []
    tool_call_blocks: list[dict] = []
    queue = _event_queue.get()
    chat_id = _chat_id_ctx.get()
    feature = f"llm_call:{chat_id}"
    start_feature(feature)
    debug_log("DEBUG", "LLM call start", chat_id=str(chat_id), tools=len(tools))

    error = await _stream_llm(
        llm, messages, tools, system, chat_id, feature, queue,
        accumulated_text, accumulated_reasoning, tool_call_blocks,
    )
    if error is not None:
        return error

    if queue is not None:
        await queue.put({"event": "thinking_done"})

    return await _build_think_result(
        accumulated_text, accumulated_reasoning, tool_call_blocks,
        executor, available_tools, llm=llm,
    )


async def _stream_llm(
    llm, messages, tools, system, chat_id, feature, queue,
    accumulated_text, accumulated_reasoning, tool_call_blocks,
) -> dict | None:
    """Drive the LLM stream. Returns error dict or None on success."""
    async for event in llm.generate_stream(messages, tools=tools, system=system, chat_id=chat_id):
        result = _process_stream_event(event, accumulated_text, accumulated_reasoning, tool_call_blocks)
        if result is True:
            complete_feature(feature)
            _log_stream_complete(accumulated_text, tool_call_blocks, chat_id)
            return None
        if result is not None:
            _log_stream_error(result, chat_id)
            complete_feature(feature, status="error")
            return {"messages": [], "tool_calls": [], "llm_error": result, "transition": Transition.ERROR_EXIT}
        if queue is not None and event["event"] == "assistant":
            await _forward_to_queue(queue, event["data"])
    return None


def _log_stream_complete(accumulated_text, tool_call_blocks, chat_id):
    tc_count = len(tool_call_blocks)
    if tc_count:
        names = [b.get("function", {}).get("name", "?") for b in tool_call_blocks]
        debug_log("DEBUG", "LLM generated tool calls", chat_id=str(chat_id),
                  count=tc_count, tools=",".join(names))
    else:
        debug_log("DEBUG", "LLM call complete (text only)", chat_id=str(chat_id),
                  text_len=len("".join(accumulated_text)))


def _log_stream_error(result, chat_id):
    debug_log("WARN", "LLM call error", chat_id=str(chat_id), code=result.get("code", "?"))
    logger.debug("think_node LLM error: code={code} msg={msg}",
                 code=result.get("code", "?"), msg=json.dumps(result.get("message", ""))[:300])


async def _forward_to_queue(queue, data_str: str) -> None:
    """Forward reasoning and content chunks to the SSE queue."""
    data = json.loads(data_str)
    rc = data.get("reasoning_content", "")
    if rc:
        await queue.put({"event": "reasoning", "data": json.dumps({"delta": rc})})
    content_chunk = data.get("delta", "")
    if content_chunk:
        await queue.put({"event": "assistant", "data": json.dumps({"delta": content_chunk})})


async def _build_think_result(accumulated_text, accumulated_reasoning, tool_call_blocks, executor, available_tools, llm=None) -> dict:
    """Assemble the final state dict from accumulated stream data."""
    result: dict = {}

    if msg := _assemble_assistant_message("".join(accumulated_text), "".join(accumulated_reasoning), tool_call_blocks):
        result["messages"] = [msg]

    if executor is not None and tool_call_blocks:
        pending_tool_calls, pre_executed = await _dispatch_tool_calls(
            tool_call_blocks, executor, available_tools
        )
    else:
        pending_tool_calls = tool_call_blocks
        pre_executed = []

    # Persist discovered tool calls to database.
    llm_trace_id = getattr(llm, "_last_trace_id", None) if llm else None
    await _persist_discovered_tools(pending_tool_calls, pre_executed, llm_trace_id)

    result["tool_calls"] = pending_tool_calls
    result["streaming_tool_results"] = pre_executed
    result["transition"] = Transition.DONE if not (pending_tool_calls or pre_executed) else None
    return result


async def _persist_discovered_tools(
    pending: list[dict],
    pre_executed: list[dict],
    llm_trace_id: UUID | None,
) -> None:
    """INSERT all discovered tool calls to DB. Stores returned UUID as call_id on each dict."""
    session_mgr = _session_manager_ctx.get()
    if session_mgr is None:
        return
    chat_id_str = _chat_id_ctx.get()
    if not chat_id_str:
        return
    chat_id = UUID(chat_id_str)
    now = datetime.now(timezone.utc)

    for tc in pending:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args_str = fn.get("arguments", "{}")
        try:
            params = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            params = {}
        tc_id_str = tc.get("id", str(uuid4()))
        try:
            msg_id = UUID(tc_id_str)
        except (ValueError, AttributeError):
            msg_id = uuid4()
        server_name = tc.get("server_name", "tool-server")

        call = ToolCall(
            name=name,
            server=ServerName(server_name) if server_name in {"tool-server", "rag-server"} else ServerName.TOOL_SERVER,
            description="",
            is_read_only=bool(tc.get("is_read_only", False)),
            is_rollbackable=bool(tc.get("is_rollbackable", False)),
            params_schema={},
            chat_id=chat_id,
            message_id=msg_id,
            params=params,
            request_id=None,
            approval_status=ApprovalStatus.PENDING,
            execution_status=ExecutionStatus.PENDING_APPROVAL,
            llm_trace_id=llm_trace_id,
            timestamp=now.isoformat(),
        )
        call_id = await session_mgr.add_tool_call(chat_id, call)
        tc["call_id"] = call_id

    for pe in pre_executed:
        tc_id_str = pe.get("tool_call_id", str(uuid4()))
        try:
            msg_id = UUID(tc_id_str)
        except (ValueError, AttributeError):
            msg_id = uuid4()
        result = pe.get("result", {})
        exec_status_str = result.get("execution_status", "SUCCEEDED")

        call = ToolCall(
            name=pe.get("tool_name", ""),
            server=ServerName.TOOL_SERVER,
            description="",
            is_read_only=True,
            is_rollbackable=False,
            params_schema={},
            chat_id=chat_id,
            message_id=msg_id,
            params={},
            request_id=None,
            approval_status=ApprovalStatus.APPROVED,
            execution_status=ExecutionStatus(exec_status_str) if exec_status_str in {"SUCCEEDED", "FAILED", "RUNNING", "PENDING_APPROVAL"} else ExecutionStatus.SUCCEEDED,
            error=result.get("error"),
            llm_trace_id=llm_trace_id,
            timestamp=now.isoformat(),
        )
        pe_call_id = await session_mgr.add_tool_call(chat_id, call)
        pe["call_id"] = pe_call_id
        # Pre-executed tools are already done; mark executed_at.
        await session_mgr.update_tool_call(
            pe_call_id, chat_id,
            execution_status=call.execution_status,
            error=call.error,
        )


def _assemble_assistant_message(text: str, reasoning: str, tool_calls: list[dict]) -> dict | None:
    """Build assistant message dict from text, reasoning, and tool calls. Returns None if empty."""
    if not (text or reasoning or tool_calls):
        return None
    msg: dict = {"role": "assistant", "content": text or ""}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _process_stream_event(
    event: dict, accumulated_text: list[str], accumulated_reasoning: list[str], tool_call_blocks: list[dict]
) -> dict | bool | None:
    if event["event"] == "assistant":
        data = json.loads(event["data"])
        accumulated_text.append(data.get("delta", ""))
        rc = data.get("reasoning_content", "")
        if rc:
            accumulated_reasoning.append(rc)
    elif event["event"] == "tool_call":
        data = json.loads(event["data"])
        _merge_tool_block(tool_call_blocks, data)
    elif event["event"] == "error":
        return json.loads(event["data"])
    elif event["event"] == "done":
        return True
    return None


def _merge_tool_block(blocks: list[dict], chunk: dict) -> None:
    fn = chunk.get("function", {})
    name = fn.get("name", "")
    args_chunk = fn.get("arguments", "")

    if name:
        blocks.append({
            "id": chunk.get("id") or str(uuid4()),
            "function": {"name": name, "arguments": args_chunk},
        })
    elif args_chunk and blocks:
        blocks[-1]["function"]["arguments"] += args_chunk
    elif args_chunk:
        blocks.append({
            "id": chunk.get("id") or str(uuid4()),
            "function": {"name": "", "arguments": args_chunk},
        })
