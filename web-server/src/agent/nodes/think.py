import json

from src.agent.nodes._helpers import _dispatch_tool_calls, _format_tools, _messages
from src.agent.results import ThinkOutput
from src.agent.mappers import (
    message_from_wire,
    message_to_db_wire,
    tool_call_from_openai,
)
from src.agent.state import LLMError, StreamChunk
from src.agent.turn_context import TurnContext
from src.observability.debug_log import log as debug_log
from src.observability.timing import complete_feature, start_feature
from src.services.llm_stream_assembly import LLMStreamAssembly, LLMStreamError


async def think_node(
    state, ctx: TurnContext | None = None, *, llm, executor=None, lifecycle=None
):
    if state.approved_tool_calls:
        return ThinkOutput()

    available_tools = state.available_tools
    tools = _format_tools(available_tools)
    messages = _messages(state)
    system = state.system
    assembly = LLMStreamAssembly()
    stream_chunks: list[StreamChunk] = []
    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    stream_sink = getattr(ctx, "stream_sink", None) if ctx else None
    feature = f"llm_call:{chat_id}"
    start_feature(feature)
    debug_log("DEBUG", "LLM call start", chat_id=str(chat_id), tools=len(tools))

    error = await _stream_llm(
        llm,
        messages,
        tools,
        system,
        chat_id,
        feature,
        assembly,
        stream_chunks,
        stream_sink=stream_sink,
    )
    if error is not None:
        return ThinkOutput(llm_error=error, is_done=True)

    return await _build_think_result(
        assembly,
        stream_chunks,
        executor,
        available_tools,
        llm=llm,
        lifecycle=lifecycle,
        ctx=ctx,
    )


async def _stream_llm(
    llm,
    messages,
    tools,
    system,
    chat_id,
    feature,
    assembly: LLMStreamAssembly,
    stream_chunks: list[StreamChunk],
    stream_sink=None,
) -> LLMError | None:
    """Drive the LLM stream. Returns an error dict or None on success."""
    emitted_live_stream = False
    try:
        async for event in llm.generate_stream(
            messages, tools=tools, system=system, chat_id=chat_id
        ):
            try:
                chunks = assembly.process(event)
            except LLMStreamError as exc:
                _log_stream_error(exc.error, messages, chat_id)
                complete_feature(feature, status="error")
                return exc.error
            except Exception as exc:
                error: LLMError = {
                    "code": 0,
                    "message": f"Stream event processing failed: {exc}",
                    "raw_event_type": event.get("event", "?")
                    if isinstance(event, dict)
                    else type(event).__name__,
                }
                _log_stream_error(error, messages, chat_id)
                complete_feature(feature, status="error")
                return error
            if assembly.done:
                if stream_sink is not None and emitted_live_stream:
                    stream_sink.emit_stream_done()
                complete_feature(feature)
                _log_stream_complete(assembly, chat_id)
                return None
            emitted_live_stream = (
                _record_stream_chunks(chunks, stream_chunks, stream_sink=stream_sink)
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


def _log_stream_complete(assembly: LLMStreamAssembly, chat_id):
    tool_calls = assembly.tool_calls
    if tool_calls:
        names = [b.get("function", {}).get("name", "?") for b in tool_calls]
        debug_log(
            "DEBUG",
            "LLM generated tool calls",
            chat_id=str(chat_id),
            count=len(tool_calls),
            tools=",".join(names),
        )
        return
    assistant_msg = assembly.assistant_message() or {}
    debug_log(
        "DEBUG",
        "LLM call complete (text only)",
        chat_id=str(chat_id),
        text_len=len(assistant_msg.get("content", "")),
    )


def _log_stream_error(result, messages, chat_id):
    from loguru import logger

    logger.error(
        "LLM call error | code={code} chat_id={chat_id} | {msg}",
        code=result.get("code", "?"),
        chat_id=str(chat_id),
        msg=str(result.get("message", ""))[:500],
    )
    debug_log(
        "WARN",
        "LLM call error",
        chat_id=str(chat_id),
        code=result.get("code", "?"),
        msg=json.dumps(result.get("message", ""))[:300],
    )
    msg_shapes = []
    for i, m in enumerate(messages):
        shape = {"idx": i, "role": m.get("role", "?"), "keys": sorted(m.keys())}
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            shape["tc_count"] = len(tcs)
            shape["tc_keys"] = [sorted(tc.keys()) for tc in tcs]
        msg_shapes.append(shape)
    debug_log(
        "DEBUG",
        "LLM message shapes",
        chat_id=str(chat_id),
        shapes=json.dumps(msg_shapes, default=str),
    )


def _record_stream_chunks(
    chunks: list[StreamChunk], stream_chunks: list[StreamChunk], stream_sink=None
) -> bool:
    """Record (type, delta) tuples for ThinkOutput.stream_chunks."""
    emitted = False
    for chunk_type, delta in chunks:
        if stream_sink is not None:
            stream_sink.emit_stream_delta(chunk_type, delta)
        else:
            stream_chunks.append((chunk_type, delta))
        emitted = True
    return emitted


async def _build_think_result(
    assembly: LLMStreamAssembly,
    stream_chunks: list[StreamChunk],
    executor,
    available_tools,
    llm=None,
    lifecycle=None,
    ctx=None,
) -> ThinkOutput:
    """Assemble ThinkOutput from accumulated stream data."""
    assistant_wire = assembly.assistant_message()
    assistant_msg = message_from_wire(assistant_wire) if assistant_wire else None
    tool_call_blocks = [tool_call_from_openai(tc) for tc in assembly.tool_calls]

    if executor is not None and tool_call_blocks:
        pending_tool_calls, pre_executed = await _dispatch_tool_calls(
            tool_call_blocks, executor, available_tools
        )
    else:
        pending_tool_calls = tool_call_blocks
        pre_executed = []

    llm_trace_id = getattr(llm, "_last_trace_id", None) if llm else None
    if lifecycle is not None:
        chat_id = ctx.chat_id if ctx else None
        if assistant_msg:
            await lifecycle.persist_assistant_message(
                chat_id, message_to_db_wire(assistant_msg)
            )
        await lifecycle.register(
            chat_id, pending_tool_calls, pre_executed, llm_trace_id
        )

    return ThinkOutput(
        assistant_message=assistant_msg,
        tool_calls=pending_tool_calls,
        pre_executed=pre_executed,
        is_done=not (pending_tool_calls or pre_executed),
        stream_chunks=stream_chunks,
    )
