import json
from uuid import uuid4

from loguru import logger

from src.agent.nodes._helpers import _format_tools, _messages
from src.agent.nodes._helpers import _dispatch_tool_calls
from src.agent.results import ThinkOutput
from src.agent.turn_context import TurnContext
from src.observability.debug_log import log as debug_log
from src.observability.timing import start_feature, complete_feature


async def think_node(state, ctx: TurnContext = None, *, llm, executor=None, lifecycle=None):
    if state.get("approved_tool_calls"):
        return ThinkOutput()

    available_tools = state.get("available_tools", [])
    tools = _format_tools(available_tools)
    messages = _messages(state)
    system = state.get("system")

    accumulated_text: list[str] = []
    accumulated_reasoning: list[str] = []
    tool_call_blocks: list[dict] = []
    stream_chunks: list[tuple] = []
    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    feature = f"llm_call:{chat_id}"
    start_feature(feature)
    debug_log("DEBUG", "LLM call start", chat_id=str(chat_id), tools=len(tools))

    error = await _stream_llm(
        llm, messages, tools, system, chat_id, feature,
        accumulated_text, accumulated_reasoning, tool_call_blocks, stream_chunks,
    )
    if error is not None:
        return ThinkOutput(llm_error=error, is_done=True)

    return await _build_think_result(
        accumulated_text, accumulated_reasoning, tool_call_blocks,
        stream_chunks, executor, available_tools, llm=llm, lifecycle=lifecycle,
        ctx=ctx,
    )


async def _stream_llm(
    llm, messages, tools, system, chat_id, feature,
    accumulated_text, accumulated_reasoning, tool_call_blocks, stream_chunks,
) -> dict | None:
    """Drive the LLM stream. Returns error dict or None on success."""
    async for event in llm.generate_stream(messages, tools=tools, system=system, chat_id=chat_id):
        result = _process_stream_event(event, accumulated_text, accumulated_reasoning, tool_call_blocks)
        if result is True:
            complete_feature(feature)
            _log_stream_complete(accumulated_text, tool_call_blocks, chat_id)
            return None
        if result is not None:
            _log_stream_error(result, messages, chat_id)
            complete_feature(feature, status="error")
            return result
        if event["event"] == "assistant":
            _record_stream_chunks(event["data"], stream_chunks)
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


def _log_stream_error(result, messages, chat_id):
    debug_log("WARN", "LLM call error", chat_id=str(chat_id), code=result.get("code", "?"))
    logger.debug("think_node LLM error: code={code} msg={msg}",
                 code=result.get("code", "?"), msg=json.dumps(result.get("message", ""))[:300])
    # Log message structure to diagnose format issues (e.g. missing type field)
    msg_shapes = []
    for i, m in enumerate(messages):
        shape = {"idx": i, "role": m.get("role", "?"), "keys": sorted(m.keys())}
        tcs = m.get("tool_calls")
        if tcs:
            shape["tc_count"] = len(tcs)
            shape["tc_keys"] = [sorted(tc.keys()) for tc in tcs]
        msg_shapes.append(shape)
    logger.debug("think_node message shapes: {shapes}", shapes=json.dumps(msg_shapes, default=str))


def _record_stream_chunks(data_str: str, stream_chunks: list[tuple]) -> None:
    """Record (type, delta) tuples for ThinkOutput.stream_chunks."""
    data = json.loads(data_str)
    rc = data.get("reasoning_content", "")
    if rc:
        stream_chunks.append(("reasoning", rc))
    content_chunk = data.get("delta", "")
    if content_chunk:
        stream_chunks.append(("assistant", content_chunk))


async def _build_think_result(accumulated_text, accumulated_reasoning, tool_call_blocks, stream_chunks, executor, available_tools, llm=None, lifecycle=None, ctx=None) -> ThinkOutput:
    """Assemble ThinkOutput from accumulated stream data."""
    assistant_msg = _assemble_assistant_message("".join(accumulated_text), "".join(accumulated_reasoning), tool_call_blocks)

    if executor is not None and tool_call_blocks:
        pending_tool_calls, pre_executed = await _dispatch_tool_calls(
            tool_call_blocks, executor, available_tools
        )
    else:
        pending_tool_calls = tool_call_blocks
        pre_executed = []

    # Persist assistant message (with or without tool_calls) so history
    # reconstruction on page reload can pair tool results correctly.
    llm_trace_id = getattr(llm, "_last_trace_id", None) if llm else None
    if lifecycle is not None:
        chat_id = ctx.chat_id if ctx else None
        if assistant_msg:
            await lifecycle.persist_assistant_message(chat_id, assistant_msg)
        await lifecycle.register(chat_id, pending_tool_calls, pre_executed, llm_trace_id)

    return ThinkOutput(
        assistant_message=assistant_msg,
        tool_calls=pending_tool_calls,
        pre_executed=pre_executed,
        is_done=not (pending_tool_calls or pre_executed),
        stream_chunks=stream_chunks,
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
            "type": "function",
            "function": {"name": name, "arguments": args_chunk},
        })
    elif args_chunk and blocks:
        blocks[-1]["function"]["arguments"] += args_chunk
    elif args_chunk:
        blocks.append({
            "id": chunk.get("id") or str(uuid4()),
            "type": "function",
            "function": {"name": "", "arguments": args_chunk},
        })
