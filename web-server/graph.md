# web-server 流程图

本文描述当前 `web-server/src/` 的实现结构。跨服务契约见 `docs/详细设计.md` 和 `docs/api-spec/openapi.yaml`。

## 总览：请求处理全链路

```mermaid
flowchart TD
    subgraph api["FastAPI /api"]
        chat["POST /api/chat<br/>SSE chat-turn"]
        sessions["GET /api/sessions<br/>GET/DELETE /api/sessions/{chat_id}"]
        tools["GET /api/tools<br/>POST /api/tools/refresh"]
        approval["POST /api/tool-requests/{request_id}/approval"]
        health["GET /api/health<br/>GET /api/metrics"]
    end

    subgraph services["Services container"]
        sm["PostgresSessionManager"]
        pm["PromptManager"]
        cm["ContextManager"]
        llm["LLMAdapter"]
        lifecycle["ToolCallLifecycle"]
        audit["PostgresAuditLogger"]
        bridge["ApprovalBridge<br/>in-memory request wait"]
        rules["RuleEngine"]
        executor["ToolExecutor"]
    end

    subgraph turn["Chat turn runtime"]
        ct["ChatTurn<br/>session/history/user message"]
        stream["SSEStream<br/>DomainEvent -> SSE wire"]
        channel["EventChannel"]
        orch["LoopOrchestrator"]
        loop["AgentLoop<br/>while-loop"]
    end

    subgraph step["AgentStep"]
        think["think_node<br/>LLM stream + tool dispatch"]
        review["review_node<br/>classify + rules"]
        act["act_node<br/>execute approved tools"]
        observe["observe_node<br/>tool messages"]
    end

    subgraph external["External"]
        provider["LLM Provider<br/>OpenAI-compatible"]
        mcp["tool-server<br/>fastmcp tools/call"]
        pg[("PostgreSQL<br/>chat_sessions/messages/tool_calls<br/>audit_events/llm_traces")]
    end

    chat --> ct
    ct --> sm
    ct --> pm
    ct --> executor
    ct --> channel
    ct --> orch
    stream --> ct

    orch --> loop
    loop --> step
    loop --> cm
    loop --> bridge
    loop --> audit

    think --> llm
    think --> executor
    think --> lifecycle
    review --> executor
    review --> rules
    review --> audit
    review --> lifecycle
    act --> executor
    act --> audit
    act --> lifecycle
    observe --> lifecycle

    llm --> provider
    executor --> mcp
    sm <--> pg
    lifecycle --> sm
    audit --> pg
    llm -. trace .-> pg

    approval --> bridge
    sessions --> sm
    tools --> executor
    health --> pg
    health --> executor

    channel --> stream
    stream --> chat
```

## API 入口

```mermaid
flowchart LR
    req["POST /api/chat<br/>{message, chat_id?}"]
    load["ChatTurn._load_session()<br/>无 chat_id 则 create_session()"]
    init["yield TurnStarted(chat_id)<br/>SSE session_init"]
    state["build initial state<br/>history + user + tools + system"]
    run["LoopOrchestrator.run(state, channel)"]
    wire["SSEStream<br/>always emits done in finally"]

    req --> load --> init --> state --> run --> wire
```

`POST /api/sessions` 当前不存在。会话由首条 `POST /api/chat` 自动创建；删除通过 `DELETE /api/sessions/{chat_id}` 软删除。

## SSE 事件映射

```mermaid
flowchart TD
    subgraph domain["DomainEvent"]
        started["TurnStarted"]
        reason["ReasoningDelta"]
        thinking_done["ThinkingDone"]
        assistant["AssistantDelta"]
        assistant_done["AssistantDone"]
        tool_started["ToolCallStarted"]
        tool_finished["ToolCallFinished"]
        approval_req["ApprovalRequired"]
        failed["TurnFailed"]
    end

    subgraph wire["SSE event"]
        e_start["session_init {chat_id}"]
        e_reason["reasoning {delta}"]
        e_thinking["thinking_done {}"]
        e_assistant["assistant {delta}"]
        e_assistant_done["assistant_done {}"]
        e_tool["tool_call {call_id, tool_name, params, is_read_only, server}"]
        e_result["tool_result {call_id, execution_status, output?, error?, execution_time_ms?}"]
        e_approval["tool_approval_required {chat_id, request_id, tool_name, params, reason, call_id}"]
        e_error["error {code, message}"]
        e_done["done {}"]
    end

    started --> e_start
    reason --> e_reason
    thinking_done --> e_thinking
    assistant --> e_assistant
    assistant_done --> e_assistant_done
    tool_started --> e_tool
    tool_finished --> e_result
    approval_req --> e_approval
    failed --> e_error
    wire --> e_done
```

SSE 首事件为 `session_init`，不再使用 `X-Session-ID` header。`SSEStream.__aiter__()` 在 `finally` 中发送 `done`。

## AgentLoop：while-loop 编排

```mermaid
stateDiagram-v2
    [*] --> Preflight
    Preflight --> TurnLimitExceeded: iteration > max
    Preflight --> CompressContext: needs_compression
    CompressContext --> TokenBudgetExceeded: still above hard ceiling
    CompressContext --> Think
    Preflight --> Think

    Think --> Done: no tool calls and no pre-executed results
    Think --> Observe: static readonly pre-executed results
    Think --> Review: static write or mutable tool calls
    Think --> ErrorRecovery: llm_error

    Review --> Act: approved tools
    Review --> ApprovalWait: pending_approval
    Review --> Observe: rejected tools

    ApprovalWait --> Act: APPROVED
    ApprovalWait --> Observe: REJECTED or EXPIRED

    Act --> Observe
    Observe --> Preflight: tool result messages appended
    ErrorRecovery --> Preflight: recovered
    ErrorRecovery --> ErrorExit: exhausted or non-recoverable

    Done --> [*]
    TurnLimitExceeded --> [*]
    TokenBudgetExceeded --> [*]
    ErrorExit --> [*]
```

## AgentStep：think → review → act → observe

```mermaid
flowchart TD
    start["AgentStep.run(state, ctx, emitter)"]
    think["think_node"]
    apply["apply ThinkOutput<br/>assistant_message/tool_calls/pre_executed/stream_chunks/llm_error"]
    emit_text["EventEmitter.emit_stream_chunks<br/>reasoning/assistant boundaries"]
    route_think{"route_after_think"}
    review["review_node"]
    route_review{"route_after_review"}
    act["act_node"]
    observe["observe_node"]
    end_node["return to AgentLoop"]

    start --> think --> apply
    apply --> emit_text
    emit_text --> route_think
    route_think -- "__end__" --> end_node
    route_think -- "observe" --> observe
    route_think -- "review" --> review
    route_think -- "act (approved_tool_calls)" --> act
    review --> route_review
    route_review -- "__end__ pending approval" --> end_node
    route_review -- "act" --> act
    act --> observe --> end_node
```

## 三池工具分配

```mermaid
flowchart TD
    llm_calls["LLM tool_calls<br/>function.name may be server__tool"]
    dispatch["_dispatch_tool_calls"]
    meta["lookup available_tools metadata"]
    mutable{"mutable?"}
    readonly{"is_read_only?"}

    safe["安全池<br/>static readonly"]
    pre_exec["execute_parallel()<br/>approval_status=APPROVED"]
    pre_results["streaming_tool_results"]

    review_pool["审查池<br/>static write + mutable"]
    classify["mutable -> {tool}_classify<br/>fail closed"]
    rule["RuleEngine.evaluate<br/>blacklist -> reject<br/>whitelist/read_only -> auto<br/>else approval"]
    approved["approved_tool_calls"]
    pending["pending_approval"]
    rejected["rejected_tool_calls"]

    llm_calls --> dispatch --> meta --> mutable
    mutable -- "false" --> readonly
    readonly -- "true" --> safe --> pre_exec --> pre_results
    readonly -- "false" --> review_pool
    mutable -- "true" --> review_pool --> classify --> rule
    review_pool --> rule
    rule --> approved
    rule --> pending
    rule --> rejected
```

## 审批与执行状态机

```mermaid
stateDiagram-v2
    [*] --> DISCOVERED: LLM tool_call
    DISCOVERED --> PRE_EXECUTED: static readonly
    PRE_EXECUTED --> SUCCEEDED
    PRE_EXECUTED --> FAILED

    DISCOVERED --> REVIEW: static write or mutable
    REVIEW --> REJECTED: blacklist
    REVIEW --> APPROVED: whitelist / readonly classification
    REVIEW --> PENDING: requires human approval

    PENDING --> APPROVED: POST approval APPROVED
    PENDING --> REJECTED: POST approval REJECTED
    PENDING --> EXPIRED: ApprovalBridge timeout

    APPROVED --> RUNNING: act_node
    RUNNING --> SUCCEEDED
    RUNNING --> FAILED

    REJECTED --> TOOL_MESSAGE: "Do NOT retry"
    EXPIRED --> TOOL_MESSAGE: timeout result
    SUCCEEDED --> TOOL_MESSAGE: output
    FAILED --> TOOL_MESSAGE: error
    TOOL_MESSAGE --> [*]
```

`SECURITY_VIOLATION` 是执行结果中的错误语义，不是 PostgreSQL `execution_status` enum。持久化时归为 `FAILED` 并记录 error。

## 审批恢复序列

```mermaid
sequenceDiagram
    participant F as Frontend
    participant API as /api/chat SSE
    participant Loop as AgentLoop
    participant AH as ApprovalHandler
    participant B as ApprovalBridge
    participant Approve as /api/tool-requests/{id}/approval
    participant Tool as tool-server

    Loop->>AH: resolve(pending_approval)
    AH->>B: create(request_id, chat_id)
    AH-->>API: ApprovalRequired DomainEvent
    API-->>F: event: tool_approval_required
    AH->>B: gather_decisions(request_id, timeout=300)
    F->>Approve: POST APPROVED / REJECTED
    Approve->>B: complete(request_id, status, reason)
    B-->>AH: decision
    alt approved
        AH->>Loop: approved_tool_calls
        Loop->>Tool: MCP tools/call with approval_status=APPROVED
        Tool-->>Loop: result
        Loop-->>API: tool_call + tool_result
    else rejected or expired
        AH->>Loop: rejected_tool_calls
        Loop-->>API: tool_result-like rejection message after observe
    end
```

## 持久化数据流

```mermaid
flowchart TD
    user["User message"] --> msg_user["messages(type=user)"]
    llm_resp["LLM stream finished"] --> trace["llm_traces"]
    llm_resp --> msg_assistant["messages(type=assistant,<br/>tool_calls, reasoning_content)"]
    tool_found["pending/pre-executed tool calls"] --> tc_insert["tool_calls INSERT"]
    review["review / approval"] --> tc_approval["tool_calls UPDATE<br/>approval_status/execution_status"]
    execute["tool execution"] --> tc_exec["tool_calls UPDATE<br/>result/error/executed_at"]
    observe["observe_node tool messages"] --> msg_tool["messages(type=tool_result,<br/>tool_call_id, tool_name)"]
    transitions["Auditor.transition / tool_event"] --> audit["audit_events INSERT"]
```

History reconstruction uses stored `messages` first. `tool_calls` remains the lifecycle ledger and compatibility fallback for older sessions.

## LLM 错误恢复

```mermaid
flowchart TD
    error["state.llm_error"]
    classify["classify_error(code, message, stop_reason)"]
    recoverable{"ErrorRecovery.get_strategy"}
    compress["compress_context / aggressive_compress"]
    tokens["escalate_max_tokens"]
    cont["append 'Please continue...'"]
    fallback["switch_to_fallback"]
    retry["clear llm_error<br/>continue next iteration"]
    surface["TurnFailed error<br/>exit loop"]

    error --> classify --> recoverable
    recoverable -- "prompt_too_long" --> compress --> retry
    recoverable -- "max_output_tokens layer 1" --> tokens --> retry
    recoverable -- "max_output_tokens layer 2, max 3" --> cont --> retry
    recoverable -- "model_unavailable/server_error" --> fallback --> retry
    recoverable -- "rate_limit/auth/timeout/unknown or exhausted" --> surface
```

## 会话生命周期

```mermaid
flowchart LR
    new_msg["POST /api/chat without chat_id"] --> create["create_session()"]
    create --> session_init["SSE session_init"]
    session_init --> active["AgentLoop active"]
    active --> persist[("PostgreSQL")]
    next_msg["POST /api/chat with chat_id"] --> load["get_session(chat_id)"]
    load --> history["build_llm_history(session)"]
    history --> active
    delete["DELETE /api/sessions/{chat_id}"] --> soft["deleted=true"]
```

## 类图

```mermaid
classDiagram
    class ChatRouter {
        +chat_turn(body, request)
    }
    class ChatTurn {
        +events() AsyncIterator~DomainEvent~
    }
    class SSEStream {
        +__aiter__()
        -_to_wire(event)
    }
    class LoopOrchestrator {
        +run(initial_state, channel)
    }
    class AgentLoop {
        +run(state, channel)
    }
    class AgentStep {
        +run(state, ctx, emitter, phase)
    }
    class ApprovalHandler {
        +resolve(scratch, turn_ctx, emitter)
    }
    class EventEmitter {
        +emit_stream_chunks(chunks)
        +emit_tools_started(tool_calls)
        +emit_tools_finished(results)
        +emit_approval_required(...)
    }
    class ApprovalBridge {
        +create(request_id, chat_id)
        +complete(request_id, status, reason)
        +gather_decisions(request_id, expected_count, timeout)
    }
    class ToolExecutor {
        +discover()
        +list_tools()
        +refresh_server(server_name)
        +classify_companion(tool_name, params, server_name)
        +execute_parallel(calls)
    }
    class PostgresSessionManager {
        +create_session()
        +get_session(chat_id)
        +add_message(chat_id, msg)
        +add_tool_call(chat_id, call)
        +update_tool_call(call_id, chat_id, ...)
        +delete_session(chat_id)
    }
    class ToolCallLifecycle {
        +persist_assistant_message(chat_id, assistant_msg)
        +persist_tool_result(chat_id, tool_messages)
        +register(chat_id, pending, pre_executed, llm_trace_id)
        +mark_approved(chat_id, call_id)
        +mark_executed(chat_id, call_id, result)
    }

    ChatRouter --> ChatTurn
    ChatTurn --> LoopOrchestrator
    ChatTurn --> SSEStream
    LoopOrchestrator --> AgentLoop
    AgentLoop --> AgentStep
    AgentLoop --> ApprovalHandler
    AgentLoop --> EventEmitter
    ApprovalHandler --> ApprovalBridge
    AgentStep --> ToolExecutor
    AgentStep --> ToolCallLifecycle
    ChatTurn --> PostgresSessionManager
    ToolCallLifecycle --> PostgresSessionManager
```

## API 数据流：用户消息到前端时间线

```mermaid
sequenceDiagram
    participant U as Browser
    participant FE as Frontend SSE client
    participant API as POST /api/chat
    participant CT as ChatTurn
    participant AL as AgentLoop
    participant EM as EventEmitter
    participant LLM as LLM Provider
    participant Tool as tool-server

    U->>FE: send message
    FE->>API: POST /api/chat
    API->>CT: ChatTurn.events()
    CT-->>API: TurnStarted(chat_id)
    API-->>FE: event: session_init
    CT->>AL: create_task(orchestrator.run)
    AL->>LLM: generate_stream(messages, tools, system)
    LLM-->>AL: reasoning/content/tool_call stream
    AL->>EM: stream chunks
    EM-->>API: ReasoningDelta / AssistantDelta / done markers
    API-->>FE: reasoning / assistant / thinking_done / assistant_done
    alt static readonly tool
        AL->>Tool: tools/call
        Tool-->>AL: result
        AL-->>API: ToolCallStarted / ToolCallFinished
        API-->>FE: tool_call / tool_result
    else high risk tool
        AL-->>API: ApprovalRequired
        API-->>FE: tool_approval_required
        FE->>API: POST /tool-requests/{id}/approval
        AL->>Tool: tools/call after approval
        Tool-->>AL: result
        API-->>FE: tool_call / tool_result
    end
    API-->>FE: done
```
