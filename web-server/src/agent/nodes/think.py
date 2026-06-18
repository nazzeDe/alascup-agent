import json
from typing import Literal
from uuid import uuid4

from src.agent.nodes._helpers import _format_tools, _messages
from src.agent.nodes._helpers import _dispatch_tool_calls
from src.agent.results import ThinkOutput
from src.agent.domain import AgentMessage, AgentToolCall, ToolFunction
from src.agent.mappers import message_to_db_wire
from src.agent.state import LLMError, StreamChunk
from src.agent.turn_context import TurnContext
from src.observability.debug_log import log as debug_log
from src.observability.timing import start_feature, complete_feature


async def think_node(
    state, ctx: TurnContext | None = None, *, llm, executor=None, lifecycle=None
):
    if state.approved_tool_calls:
        return ThinkOutput()

    available_tools = state.available_tools
    tools = _format_tools(available_tools)
    messages = _messages(state)
    system = state.system

    accumulated_text: list[str] = []
    accumulated_reasoning: list[str] = []
    tool_call_blocks: list[AgentToolCall] = []
    stream_chunks: list[StreamChunk] = []
    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    stream_sink = getattr(ctx, "stream_sink", None) if ctx else None
    feature = f"llm_call:{chat_id}"
    start_feature(feature)
    debug_log("DEBUG", "LLM call start", chat_id=str(chat_id), tools=len(tools))

    error = await _stream_llm(
        llm, messages, tools, system, chat_id, feature,
        accumulated_text, accumulated_reasoning, tool_call_blocks, stream_chunks,
        stream_sink=stream_sink,
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
    stream_sink=None,
) -> LLMError | None:
    """Drive the LLM stream. Returns error dict or None on success."""
    emitted_live_stream = False
    try:
        async for event in llm.generate_stream(messages, tools=tools, system=system, chat_id=chat_id):
            try:
                result = _process_stream_event(event, accumulated_text, accumulated_reasoning, tool_call_blocks)
            except Exception as exc:
                error: LLMError = {
                    "code": 0,
                    "message": f"Stream event processing failed: {exc}",
                    "raw_event_type": event.get("event", "?") if isinstance(event, dict) else type(event).__name__,
                }
                _log_stream_error(error, messages, chat_id)
                complete_feature(feature, status="error")
                return error
            if result is True:
                if stream_sink is not None and emitted_live_stream:
                    stream_sink.emit_stream_done()
                complete_feature(feature)
                _log_stream_complete(accumulated_text, tool_call_blocks, chat_id)
                return None
            if result is not None:
                _log_stream_error(result, messages, chat_id)
                complete_feature(feature, status="error")
                return result
            if event["event"] == "assistant":
                emitted_live_stream = (
                    _record_stream_chunks(event["data"], stream_chunks, stream_sink=stream_sink)
                    or emitted_live_stream
                )
    except Exception as exc:
        error = {
            "code": 0,
            "message": f"LLM stream failed: {exc}",
        }
        _log_stream_error(error, messages, chat_id)
        complete_feature(feature, status="error")
        return error
    return None


def _log_stream_complete(accumulated_text, tool_call_blocks, chat_id):
    tc_count = len(tool_call_blocks)
    if tc_count:
        names = [b.function.name for b in tool_call_blocks]
        debug_log("DEBUG", "LLM generated tool calls", chat_id=str(chat_id),
                  count=tc_count, tools=",".join(names))
    else:
        debug_log("DEBUG", "LLM call complete (text only)", chat_id=str(chat_id),
                  text_len=len("".join(accumulated_text)))


def _log_stream_error(result, messages, chat_id):
    from loguru import logger
    logger.error(
        "LLM call error | code={code} chat_id={chat_id} | {msg}",
        code=result.get("code", "?"),
        chat_id=str(chat_id),
        msg=str(result.get("message", ""))[:500],
    )
    debug_log("WARN", "LLM call error", chat_id=str(chat_id),
              code=result.get("code", "?"),
              msg=json.dumps(result.get("message", ""))[:300])
    # Log message structure to diagnose format issues (e.g. missing type field)
    msg_shapes = []
    for i, m in enumerate(messages):
        shape = {"idx": i, "role": m.get("role", "?"), "keys": sorted(m.keys())}
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            shape["tc_count"] = len(tcs)
            shape["tc_keys"] = [sorted(tc.keys()) for tc in tcs]
        msg_shapes.append(shape)
    debug_log("DEBUG", "LLM message shapes", chat_id=str(chat_id), shapes=json.dumps(msg_shapes, default=str))


def _record_stream_chunks(
    data_str: str, stream_chunks: list[StreamChunk], stream_sink=None
) -> bool:
    """Record (type, delta) tuples for ThinkOutput.stream_chunks."""
    data = json.loads(data_str)
    emitted = False
    rc = data.get("reasoning_content", "")
    if rc:
        if stream_sink is not None:
            stream_sink.emit_stream_delta("reasoning", rc)
        else:
            stream_chunks.append(("reasoning", rc))
        emitted = True
    content_chunk = data.get("delta", "")
    if content_chunk:
        if stream_sink is not None:
            stream_sink.emit_stream_delta("assistant", content_chunk)
        else:
            stream_chunks.append(("assistant", content_chunk))
        emitted = True
    return emitted


async def _build_think_result(
    accumulated_text,
    accumulated_reasoning,
    tool_call_blocks: list[AgentToolCall],
    stream_chunks: list[StreamChunk],
    executor,
    available_tools,
    llm=None,
    lifecycle=None,
    ctx=None,
) -> ThinkOutput:
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
            await lifecycle.persist_assistant_message(chat_id, message_to_db_wire(assistant_msg))
        await lifecycle.register(chat_id, pending_tool_calls, pre_executed, llm_trace_id)

    return ThinkOutput(
        assistant_message=assistant_msg,
        tool_calls=pending_tool_calls,
        pre_executed=pre_executed,
        is_done=not (pending_tool_calls or pre_executed),
        stream_chunks=stream_chunks,
    )


def _assemble_assistant_message(
    text: str, reasoning: str, tool_calls: list[AgentToolCall]
) -> AgentMessage | None:
    """Build assistant message dict from text, reasoning, and tool calls. Returns None if empty."""
    if not (text or reasoning or tool_calls):
        return None
    return AgentMessage(
        role="assistant",
        content=text or "",
        reasoning_content=reasoning or None,
        tool_calls=tool_calls,
    )


def _process_stream_event(
    event: dict,
    accumulated_text: list[str],
    accumulated_reasoning: list[str],
    tool_call_blocks: list[AgentToolCall],
) -> LLMError | Literal[True] | None:
    if not isinstance(event, dict) or "event" not in event:
        raise ValueError(f"Invalid stream event — expected dict with 'event' key: {type(event).__name__}")
    event_type = event["event"]
    if event_type == "assistant":
        data = json.loads(event["data"])
        accumulated_text.append(data.get("delta", ""))
        rc = data.get("reasoning_content", "")
        if rc:
            accumulated_reasoning.append(rc)
    elif event_type == "tool_call":
        data = json.loads(event["data"])
        _merge_tool_block(tool_call_blocks, data)
    elif event_type == "error":
        return json.loads(event["data"])
    elif event_type == "done":
        return True
    return None


def _merge_tool_block(blocks: list[AgentToolCall], chunk: dict) -> None:
    fn = chunk.get("function", {})
    name = fn.get("name", "")
    args_chunk = fn.get("arguments", "")

    if name:
        blocks.append(AgentToolCall(
            id=chunk.get("id") or str(uuid4()),
            function=ToolFunction(name=name, arguments=_parse_partial_args(args_chunk)),
        ))
    elif args_chunk and blocks:
        current = blocks[-1].function.arguments
        existing = current.get("_raw", json.dumps(current) if current else "")
        blocks[-1].function.arguments = _parse_partial_args(existing + args_chunk)
    elif args_chunk:
        blocks.append(AgentToolCall(
            id=chunk.get("id") or str(uuid4()),
            function=ToolFunction(name="", arguments=_parse_partial_args(args_chunk)),
        ))


def _parse_partial_args(value: str) -> dict:
    try:
        return json.loads(value) if value else {}
    except json.JSONDecodeError:
        return {"_raw": value}
