# Web-server architecture deepening — completion handoff

Date: 2026-06-11
Session: All 7 issues (#24–#30) implemented and tested.
Reference: `docs/handoffs/2026-06-11-web-server-architecture-deepening.md`

## Completed

All seven `ready-for-agent` issues from the architecture deepening are implemented.

| # | Issue | Status | Key changes |
|---|-------|--------|-------------|
| 24 | Wire CircuitBreaker, remove inline duplicates | Done | `LoopOrchestrator` delegates to `CircuitBreaker`; `_inject_turn_hint`, `_last_hint` removed |
| 25 | Delete EmissionTracker + tracker path | Done | `emission.py` deleted; `emit_events` tracker parameter removed; tracker branch deleted |
| 30 | env vars → Settings | Done | `agent_max_iterations`/`agent_token_ceiling_ratio` flow: Settings → _build_services → Services → chat.py → Query → LoopOrchestrator. No `os.getenv` in `src/agent/` |
| 26 | TurnContext + Auditor, remove state `_` smuggling | Done | `TurnContext` (frozen) + `Auditor` in `src/agent/turn_context.py`; `_turn_id`/`_iteration`/`_model` removed from `AgentState`; all audit call sites collapsed to `auditor.transition()`/`auditor.tool_event()` |
| 27 | Normalize messages at graph boundary | Done | `src/agent/messages.py` with `normalize_message()`/`normalize_state_messages()`; deleted `_msg_role`/`_msg_id`/`_msg_content`/`_is_assistant`/`_to_dict`/`_normalize_message`/`_extract_meta`/`_fill_dict_meta`/`_fill_obj_meta` |
| 28 | Typed DomainEvent channel + ChatTurn | Done | `src/agent/events.py` (frozen DomainEvent union + EventChannel); `src/chat_turn.py` (ChatTurn owns session/persistence/channel); SSEStream → stateless wire adapter (101 lines); `loop/events.py` and `query.py` deleted; frontend updated to new wire contract |
| 29 | Single graph driver | Done | `ApprovalHandler.resolve` no longer calls `graph.ainvoke` — pure side-effecting helper; orchestrator re-invokes graph after approval; think_node fast-path preserved |

## Architecture achieved

```
ChatTurn (session lifecycle, persistence, channel ownership)
  └─ EventChannel (asyncio.Queue[DomainEvent | None])
       ├─ LoopOrchestrator.run(state, channel) → None  (sole graph driver)
       │    ├─ think_node → sends ReasoningDelta/AssistantDelta/ThinkingDone/AssistantDone
       │    ├─ review_node → classifies tools
       │    ├─ ApprovalHandler.resolve → ApprovalRequired events, applies decisions
       │    ├─ act_node → sends ToolCallStarted/ToolCallFinished
       │    └─ circuit breaker via CircuitBreaker
       └─ SSEStream (stateless wire adapter: DomainEvent → SSE text, done in finally)

Frontend (Vue):
  session_init → stores chat_id
  reasoning → delta-only accumulation
  thinking_done → closes reasoning bubble
  assistant → delta-only accumulation
  assistant_done → commits message (generates message_id locally)
  tool_call → call_id (was: message_id)
  tool_result → call_id
  done → SSEStream finally only
```

## Wire contract (current)

| SSE event | data |
|---|---|
| `session_init` | `{chat_id}` |
| `reasoning` | `{delta}` |
| `thinking_done` | `{}` |
| `assistant` | `{delta}` |
| `assistant_done` | `{}` |
| `tool_call` | `{call_id, tool_name, params, is_read_only, server}` |
| `tool_result` | `{call_id, execution_status, output?, error?, execution_time_ms?}` |
| `tool_approval_required` | `{chat_id, request_id, tool_name, params, reason}` |
| `error` | `{code, message}` |
| `done` | `{}` |

## New files

- `web-server/src/agent/events.py` — DomainEvent union + EventChannel
- `web-server/src/agent/messages.py` — single message normalization
- `web-server/src/agent/turn_context.py` — TurnContext + Auditor
- `web-server/src/chat_turn.py` — ChatTurn lifecycle manager

## Deleted files

- `web-server/src/agent/loop/emission.py`
- `web-server/src/agent/loop/events.py` (the old emit_events module)
- `web-server/src/agent/query.py`

## Test status

- Backend: 336 passed, 1 pre-existing failure (`test_health_returns_ok` — env-dependent)
- Frontend: 35 passed (3 test files, all green)

## What's out of scope (unchanged)

- `MCP_client/` and `services/llm_adapter.py` internals
- `Services` dataclass shape (other than adding agent limit fields)
- Backward-compatible wire-contract shims
- ADR-0001 (refined but not contradicted)

## Suggested approach for E2E verification

1. Start services (see CLAUDE.md for commands):
   - web-server on 11450
   - frontend on 5173
2. Open `http://localhost:5173` in Chrome
3. Send a message like "what tools are available?"
4. Verify SSE events match the wire contract above
5. Test approval flow: send a message that triggers a high-risk tool (e.g., "restart the service")
6. Verify `tool_approval_required` → approve → `tool_call` → `tool_result` → `assistant` → `assistant_done`

## Suggested skills

- `chrome-devtools` MCP tools — for browser-based E2E testing
- `run` — to launch the app if services aren't running
- `verification-before-completion` — before declaring anything done
