"""Diagnostic trace point constants for debug_log.

Use these instead of ad-hoc message strings so ``grep 'think_done'
logs/debug/latest`` pinpoints exactly the right moment.
"""

# ── Agent loop phases ────────────────────────────────────────────
LOOP_START = "loop_start"
LOOP_ITERATION = "loop_iteration"
THINK_START = "think_start"
THINK_DONE = "think_done"
THINK_ERROR = "think_error"
REVIEW_ENTER = "review_enter"
REVIEW_DONE = "review_done"
REVIEW_PENDING = "review_pending"
ACT_ENTER = "act_enter"
ACT_DONE = "act_done"
OBSERVE_DONE = "observe_done"
ROUTE_AFTER_THINK = "route_after_think"
ROUTE_AFTER_REVIEW = "route_after_review"
TRANSITION = "transition"
LOOP_EXIT = "loop_exit"

# ── SSE / wire protocol ──────────────────────────────────────────
SSE_DONE = "sse_done"
SSE_TOOL_STARTED = "sse_tool_started"
SSE_TOOL_FINISHED = "sse_tool_finished"
SSE_TURN_FAILED = "sse_turn_failed"
SSE_UNHANDLED = "sse_unhandled"

# ── Event channel ─────────────────────────────────────────────────
EVENT_SEND = "event_send"
EVENT_SEND_DROPPED = "event_send_dropped"
EVENT_RECEIVE = "event_receive"
EVENT_CLOSE = "event_close"

# ── ChatTurn ──────────────────────────────────────────────────────
CHAT_TURN_RECEIVED = "chat_turn_received"
CHAT_TURN_DRAIN_START = "chat_turn_drain_start"
CHAT_TURN_DRAIN_DONE = "chat_turn_drain_done"
CHAT_TURN_YIELD = "chat_turn_yield"

# ── EventEmitter ──────────────────────────────────────────────────
EMIT_TOOL_STARTED = "emit_tool_started"
EMIT_TOOL_FINISHED = "emit_tool_finished"

# ── Infrastructure ────────────────────────────────────────────────
CONTEXT_COMPRESS = "context_compress"
TOKEN_CEILING = "token_ceiling"  # noqa: S105 - trace point name, not secret.
TURN_LIMIT = "turn_limit"
ERROR_RECOVERY = "error_recovery"
LLM_ERROR_DETAIL = "llm_error_detail"
LLM_MESSAGE_SHAPES = "llm_message_shapes"
