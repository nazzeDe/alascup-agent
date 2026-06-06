import contextvars

# Per-chat-turn queue for streaming reasoning tokens to the SSE handler.
# Set by Query.chat() before agent execution, read by think_node during LLM streaming.
_event_queue: contextvars.ContextVar = contextvars.ContextVar("event_queue", default=None)

# Per-chat-turn chat_id for passing to LLM adapter (tracing, logging).
# Set by Query.chat() before agent execution, read by think_node.
_chat_id_ctx: contextvars.ContextVar = contextvars.ContextVar("chat_id_ctx", default=None)

from src.agent.nodes.think import think_node, _merge_tool_block
from src.agent.nodes.review import review_node
from src.agent.nodes.act import act_node
from src.agent.nodes.observe import observe_node, route_after_review, route_after_think
from src.agent.nodes._message_format import _messages, _format_tools
from src.agent.nodes._tool_dispatch import _parse_args

__all__ = [
    "think_node",
    "review_node",
    "act_node",
    "observe_node",
    "route_after_review",
    "route_after_think",
    "_messages",
    "_merge_tool_block",
    "_format_tools",
    "_parse_args",
]
