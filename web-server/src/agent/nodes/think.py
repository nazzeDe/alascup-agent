import json
from uuid import uuid4

from loguru import logger

from src.agent.nodes import _event_queue, _chat_id_ctx
from src.agent.nodes._message_format import _format_tools, _messages
from src.agent.nodes._tool_dispatch import _dispatch_tool_calls
from src.agent.state import Transition


async def think_node(state, *, llm, executor=None):
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
    accumulated_reasoning: list[str] = []
    tool_call_blocks: list[dict] = []
    queue = _event_queue.get()
    chat_id = _chat_id_ctx.get()

    async for event in llm.generate_stream(messages, tools=tools, system=system, chat_id=chat_id):
        result = _process_stream_event(event, accumulated_text, accumulated_reasoning, tool_call_blocks)
        if result is True:
            break
        if result is not None:
            logger.debug("think_node LLM error: code={code} msg={msg}",
                         code=result.get("code", "?"), msg=json.dumps(result.get("message", ""))[:300])
            return {
                "messages": [],
                "tool_calls": [],
                "llm_error": result,
                "transition": Transition.ERROR_EXIT,
            }
        if queue is not None and event["event"] == "assistant":
            data = json.loads(event["data"])
            rc = data.get("reasoning_content", "")
            if rc:
                await queue.put({"event": "reasoning", "data": json.dumps({"delta": rc})})

    if queue is not None:
        await queue.put({"event": "thinking_done"})

    result: dict = {}
    text = "".join(accumulated_text)
    reasoning = "".join(accumulated_reasoning)

    if text or reasoning or tool_call_blocks:
        assistant_msg: dict = {"role": "assistant", "content": text or ""}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        if tool_call_blocks:
            assistant_msg["tool_calls"] = tool_call_blocks
        result["messages"] = [assistant_msg]

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
