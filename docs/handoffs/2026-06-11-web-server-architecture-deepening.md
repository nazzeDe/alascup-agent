# Web-server architecture deepening — handoff

Date: 2026-06-11
Scope: `web-server/` only (frontend touches arrive in slice D).

Outcome: seven `ready-for-agent` issues filed against `nazzeDe/alascup-agent`, ordered by dependency. Issues #24–#30.

## Why

`web-server/` accumulated three structural faults that compound each other:

1. **Dead parallel implementations.** `CircuitBreaker` and `EmissionTracker` were extracted with full test coverage but have zero production callers; `LoopOrchestrator` inlines the same logic. Tests pass on dead code → false confidence.
2. **Magic-key dict events end-to-end.** SSE events are `{"event": str, "data": json_str}` from the moment they leave a node. `SSEStream` then `json.loads` its own outbound events to recover the assistant text for persistence. Two assistant channels (real-time deltas without `message_id`, post-hoc full message with `message_id`) exist solely so persistence has something to anchor on; `EmissionTracker.skip_assistant_count` exists to dedupe between them.
3. **Pass-through layers.** `Query` is an 11-parameter pass-through to `LoopOrchestrator`. `ApprovalHandler` calls `graph.ainvoke` itself, in parallel with the orchestrator's own call — `think_node` has a fast-path solely to skip the second invocation. Observability context (`chat_id`/`turn_id`/`iteration`/`model`) is smuggled through `AgentState` via underscore-prefixed keys.

The fix is a single direction: source-side typed events on a single channel, with `LoopOrchestrator` as the sole graph driver.

## Decisions reached during grilling

These are load-bearing; future architecture reviews shouldn't re-litigate them without reading this.

- **Typed `DomainEvent` union, not magic-key dicts.** Frozen dataclasses defined in `src/agent/events.py`; serialization happens once at the SSEStream wire-adapter boundary.
- **Single `EventChannel`** (`asyncio.Queue[DomainEvent | None]` + `close()` sentinel). All emitters — nodes, `ApprovalHandler`, orchestrator error paths — write to one channel. Orchestrator's `run()` becomes `async def run(state) -> None`, not an `AsyncIterator`.
- **Claude-style assistant streaming.** `AssistantDelta` carries `delta` only — no `message_id` on the wire. `AssistantDone` marks message boundary. Persistence accumulates between boundaries on the consumer side (ChatTurn); the `message_id` is generated at persistence time and never crosses the wire.
- **`done` is not a domain event.** Iterator exhaustion = end of turn. SSEStream emits the wire `done` exactly once in its `finally` — single source of truth, replacing the 6+ scattered hand-yielded `done` events today.
- **Tool events keep `call_id`.** Renamed from the misnamed `message_id` field. Needed for request_id ↔ result correlation (mirrors Claude's `tool_use.id`).
- **`chat_id` only on `session_init`.** Not repeated on every event; the stream is the session.
- **Orchestrator is the only `graph.ainvoke` caller.** `ApprovalHandler` becomes a pure side-effecting helper (emit events, wait on bridge, apply decisions, return). `think_node` fast-path goes away.
- **Backward compatibility is not a constraint.** Frontend updates synchronously within slice D.
- **Tests adapt to channel-based assertions** via a `collect_events(channel, timeout)` helper. The orchestrator's `close()` in `finally` is what makes this safe.
- **A2 (`EmissionTracker` removal) descopes to "delete, don't wire"** because D makes `emit_events` dead in its entirety. No point wiring something we're about to delete.
- **Issue F (env → Settings) stays standalone** — independent of the rest, can ship anytime.

## Final wire contract (defined in slice D)

| SSE event | data |
|---|---|
| `session_init` | `{chat_id}` |
| `reasoning` | `{delta}` |
| `thinking_done` | `{}` |
| `assistant` | `{delta}` |
| `assistant_done` | `{}` (new) |
| `tool_call` | `{call_id, tool_name, params, is_read_only, server}` |
| `tool_result` | `{call_id, execution_status, output?, error?, execution_time_ms?}` |
| `tool_approval_required` | `{chat_id, request_id, tool_name, params, reason}` |
| `error` | `{code, message}` |
| `done` | `{}` (SSEStream `finally`) |

## Issues filed (dependency-ordered)

| # | Issue | Title | Blocked by |
|---|-------|-------|------------|
| 1 | [#24](https://github.com/nazzeDe/alascup-agent/issues/24) | Wire `CircuitBreaker` into orchestrator, remove inline duplicates | — |
| 2 | [#25](https://github.com/nazzeDe/alascup-agent/issues/25) | Delete `EmissionTracker` and `emit_events` tracker path | — |
| 3 | [#26](https://github.com/nazzeDe/alascup-agent/issues/26) | `TurnContext` + `Auditor`, remove state `_` smuggling | #24, #25 |
| 4 | [#27](https://github.com/nazzeDe/alascup-agent/issues/27) | Normalize messages at graph boundary, remove 4 duplicate helpers | #25 |
| 5 | [#28](https://github.com/nazzeDe/alascup-agent/issues/28) | Typed `DomainEvent` channel + `ChatTurn`, retire `SSEStream` double-queue (D) | #26, #27 |
| 6 | [#29](https://github.com/nazzeDe/alascup-agent/issues/29) | Single graph driver — `ApprovalHandler` stops calling `graph.ainvoke` (E) | #28 |
| 7 | [#30](https://github.com/nazzeDe/alascup-agent/issues/30) | Move `AGENT_MAX_ITERATIONS` / `AGENT_TOKEN_CEILING_RATIO` into `Settings` (F) | — |

## Recommended execution order

#24 and #25 can ship in parallel (different files; one wires, one deletes). #30 can ship anytime — no overlap.

After both #24 and #25 land, #26 follows; #27 needs #25 only. Then #28 is the heavy lift (the D slice — touches orchestrator, nodes, SSEStream, frontend). #29 mops up the approval driver once D is in.

## What is explicitly out of scope

- Splitting the `Services` dataclass / DI container — current shape is fine for now.
- Touching `MCP_client/` or `services/llm_adapter.py` internals.
- Backward-compatible wire-contract shims for older frontends.
- Any rework of `ADR-0001` (SSE stream session-id) — the contract change in D refines it but doesn't contradict its principles. If reviewers think it needs an ADR superseder, raise it on issue #28.
