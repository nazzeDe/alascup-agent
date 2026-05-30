# web-server 流程图

## 总览：请求处理全链路

```mermaid
flowchart TD
    %% ============================================================
    %% 1. 节点声明（Declaration）
    %% ============================================================

    %% ── API 入口层 ──
    api_chat_turn["POST /api/chat<br/>聊天回合（SSE 流式）"]
    api_sessions_list["GET /api/sessions<br/>会话列表"]
    api_sessions_create["POST /api/sessions<br/>创建会话"]
    api_session_detail["GET /api/sessions/{chat_id}<br/>会话详情"]
    api_tools["GET /api/tools<br/>工具列表"]
    api_toolreq_approval["POST /api/tool-requests/{request_id}/approval<br/>审批回调"]
    api_health["GET /api/health<br/>健康检查"]

    %% ── 服务层 ──
    svc_session_mgr["Session Manager<br/>加载 / 创建 ChatSession"]
    svc_prompt_mgr["Prompt Manager<br/>组装 system prompt（section-based）"]
    svc_llm_adapter["LLM Adapter<br/>OpenAI 兼容调用"]

    %% ── Agent 编排层（ReAct 循环）──
    agent_state[(Agent State<br/>消息 + 上下文 + 工具注册)]
    agent_think["LLM 流式调用<br/>yield assistant delta"]
    agent_router{"Router<br/>响应类型？"}
    agent_text["AssistantMessage<br/>文本回复"]
    agent_plan_check{"LLM 自主判断<br/>是否需先输出计划？"}
    agent_toolcall["ToolCallMessage<br/>工具调用"]
    agent_act["执行工具<br/>只读并行 / 高风险串行"]
    agent_observe["处理 ToolResult<br/>追加到消息历史"]
    agent_decide{"继续循环<br/>还是退出？"}

    %% ── 审查层 ──
    sec_entry["审查层入口<br/>静态非只读工具 + 可变工具"]
    sec_rule_engine{"Rule Engine<br/>rules.json 匹配"}
    sec_blacklist["黑名单拒绝<br/>审计: WARN"]
    sec_auto_approve["自动审批通过<br/>审计: INFO / WARN"]
    sec_create_request["生成 ToolRequest<br/>状态: PENDING"]
    sec_readonly_pre["静态只读工具预执行<br/>think 节点内直接执行<br/>绕过审查层（AG-007）"]
    sec_state_machine[(ToolRequest 状态机<br/>PENDING → APPROVED / REJECTED / EXPIRED)]
    sec_wait_approval["等待用户确认<br/>5 分钟超时"]
    sec_approval_result{"审批结果？"}
    sec_approved["APPROVED<br/>携带 approval_status"]
    sec_rejected["REJECTED<br/>通知 LLM 被拒原因"]
    sec_expired["EXPIRED<br/>通知 LLM 超时失效"]

    %% ── MCP 工具执行层 ──
    mcp_client["MCP Client (fastmcp)<br/>懒连接 JSON-RPC"]
    mcp_tool_server["调用 tool-server"]
    mcp_rag_server["调用 rag-server<br/>"]
    mcp_conn_retry{"连接成功？"}
    mcp_retry["重试 2 次<br/>间隔 1s / 2s"]
    mcp_result{"ToolResult<br/>execution_status？"}
    mcp_success["SUCCEEDED<br/>结构化结果"]
    mcp_failed["FAILED<br/>错误信息"]
    mcp_timeout["调用超时<br/>标记 FAILED"]
    mcp_violation["SECURITY_VIOLATION<br/>tool-server 二次校验失败"]

    %% ── SSE 流式输出层 ──
    sse_assistant("event: assistant<br/>LLM 文本增量")
    sse_toolcall("event: tool_call<br/>工具调用通知")
    sse_toolresult("event: tool_result<br/>工具执行结果")
    sse_approval_req("event: tool_approval_required<br/>高风险审批请求")
    sse_error("event: error<br/>异常通知")
    sse_done("event: done<br/>流结束")

    %% ── 观测层 ──
    obs_audit["审计日志<br/>PostgreSQL audit_events 表"]
    obs_app["应用日志<br/>servers.log"]
    obs_tracer["自定义 Tracer<br/>PostgreSQL llm_traces 表<br/>token / 延迟 / 响应追踪"]

    %% ── 外部存储 ──
    db_postgres[("PostgreSQL<br/>chat_sessions / messages<br/>tool_calls<br/>audit_events / llm_traces")]

    %% ── 外部系统 ──
    ext_llm["LLM Provider<br/>OpenAI 兼容 API"]
    ext_tool["tool-server<br/>系统感知 + 运维操作"]
    ext_rag["rag-server<br/>运维经验库"]


    %% ============================================================
    %% 2. 逻辑连线（Connections）
    %% ============================================================

    %% ── 请求入口 → 服务层 ──
    api_chat_turn --> svc_session_mgr
    api_sessions_create --> svc_session_mgr
    api_session_detail --> svc_session_mgr
    svc_session_mgr <--> db_postgres

    %% ── 服务层 → Agent 编排 ──
    svc_session_mgr --> svc_prompt_mgr
    svc_prompt_mgr --> agent_state

    %% ── Agent ReAct 循环 ──
    agent_state --> agent_think
    agent_think --> svc_llm_adapter
    svc_llm_adapter --> ext_llm
    ext_llm --> agent_router

    %% 文本分支
    agent_router -- "文本回复" --> agent_text
    agent_text --> agent_plan_check
    agent_plan_check -- "简单问题" --> sse_assistant
    agent_plan_check -- "高风险操作<br/>先输出分析计划" --> sse_assistant
    sse_assistant --> agent_decide

    %% 工具调用分支
    agent_router -- "tool_call" --> agent_toolcall
    agent_toolcall --> sec_readonly_pre
    sec_readonly_pre -- "静态只读" --> agent_observe
    agent_toolcall -- "静态写 / 可变" --> sec_entry

    %% ── 审查层 ──
    sec_entry --> sec_rule_engine
    sec_rule_engine -- "黑名单命中" --> sec_blacklist
    sec_blacklist --> obs_audit
    sec_blacklist -- "拒绝原因" --> agent_observe

    sec_rule_engine -- "只读 / 白名单" --> sec_auto_approve
    sec_auto_approve --> obs_audit
    sec_auto_approve --> agent_act

    sec_rule_engine -- "高风险 / 未命中默认" --> sec_create_request
    sec_create_request --> sec_state_machine
    sec_state_machine --> sse_approval_req
    sse_approval_req --> sec_wait_approval
    sec_wait_approval --> api_toolreq_approval
    api_toolreq_approval --> sec_approval_result
    sec_approval_result -- "APPROVED" --> sec_approved
    sec_approved --> obs_audit
    sec_approved --> agent_act
    sec_approval_result -- "REJECTED" --> sec_rejected
    sec_rejected --> obs_audit
    sec_rejected -- "被拒原因" --> agent_observe
    sec_wait_approval -- "5 分钟超时" --> sec_expired
    sec_expired --> obs_audit
    sec_expired -- "超时通知" --> agent_observe

    %% ── MCP 工具执行 ──
    agent_act --> mcp_client
    mcp_client --> mcp_conn_retry
    mcp_conn_retry -- "成功" --> mcp_tool_server
    mcp_conn_retry -- "成功" --> mcp_rag_server
    mcp_conn_retry -- "失败" --> mcp_retry
    mcp_retry -- "重试成功" --> mcp_tool_server
    mcp_retry -- "2 次仍失败" --> obs_audit
    mcp_retry -- "2 次仍失败<br/>反馈 LLM: tool unavailable" --> agent_observe

    mcp_tool_server --> ext_tool
    mcp_rag_server --> ext_rag
    ext_tool --> mcp_result
    ext_rag --> mcp_result

    mcp_result -- "SUCCEEDED" --> mcp_success
    mcp_result -- "FAILED" --> mcp_failed
    mcp_result -- "超时" --> mcp_timeout
    mcp_result -- "SECURITY_VIOLATION" --> mcp_violation

    mcp_success --> sse_toolcall
    mcp_success --> sse_toolresult
    mcp_success --> agent_observe

    mcp_failed --> obs_audit
    mcp_failed --> agent_observe

    mcp_timeout --> obs_audit
    mcp_timeout --> agent_observe

    mcp_violation --> obs_audit
    mcp_violation --> sse_error

    %% ── Observation 回到循环 ──
    agent_observe --> agent_state

    %% ── 循环终止 ──
    agent_decide -- "继续推理" --> agent_state
    agent_decide -- "结束" --> sse_done
    sse_done --> obs_audit

    %% ── 观测层 ──
    svc_llm_adapter -.-> obs_tracer
    agent_think -.-> obs_tracer
    mcp_retry -.-> obs_app
    mcp_violation -.-> obs_app
    sse_error -.-> obs_app
```

---

## 审批与执行：ToolRequest / ToolCall 状态机

```mermaid
stateDiagram-v2
    state "审查层入口" as SEC_ENTRY

    [*] --> SEC_ENTRY: LLM 生成 tool_call

    SEC_ENTRY --> AUTO_APPROVED: 只读 / 白名单
    SEC_ENTRY --> DIRECT_REJECTED: 黑名单
    SEC_ENTRY --> PENDING: 高风险 / 未命中默认

    AUTO_APPROVED --> RUNNING: 直接执行
    DIRECT_REJECTED --> [*]: 通知 LLM 拒绝

    PENDING --> APPROVED: 用户批准
    PENDING --> REJECTED: 用户拒绝
    PENDING --> EXPIRED: 5 分钟超时

    APPROVED --> RUNNING: 携带 approval_status=APPROVED
    REJECTED --> [*]: 通知 LLM 被拒原因
    EXPIRED --> [*]: 通知 LLM 超时

    RUNNING --> SUCCEEDED: tool-server 执行成功
    RUNNING --> FAILED: tool-server 执行失败
    RUNNING --> SECURITY_VIOLATION: approval_status ≠ APPROVED

    SUCCEEDED --> [*]: 结果回写 Agent State
    FAILED --> [*]: 错误回写 Agent State
    SECURITY_VIOLATION --> [*]: 记录 CRITICAL 审计
```

---

## Agent 循环：StateGraph + 编排器驱动

LangGraph StateGraph 管理节点拓扑。Graph 是纯函数（无 checkpointer、无 interrupt/resume），编排器（LoopOrchestrator）通过 `ainvoke()` 调用图并驱动审批循环。SSE 连接在审批等待期间保持存活，审批决策通过 REST 传入后由编排器合并到状态中并重新调用图。

```mermaid
stateDiagram-v2
    [*] --> INIT: POST /api/chat

    state "会话准备" as PREP {
        INIT --> LOAD_SESSION: 加载 / 创建 ChatSession
        LOAD_SESSION --> INJECT_PROMPT: 注入 system prompt
        INJECT_PROMPT --> READY: 进入 Agent State
    }

    READY --> AGENT_STATE: 初始消息 + 工具列表

    state AGENT_STATE {
        MSGS: messages[]
        TOOLS: available_tools[]
    }

    AGENT_STATE --> THINK: 输出上下文到 LLM
    THINK --> LLM_CALL: 调用 LLM（流式）
    LLM_CALL --> ROUTE: 解析响应

    ROUTE --> TEXT: AssistantMessage
    ROUTE --> TOOL: ToolCallMessage

    TEXT --> DONE: 无 tool_call → 退出
    DONE --> [*]: SSE done 事件

    TOOL --> THINK_PRE: 静态只读 → 预执行
    TOOL --> REVIEW: 静态写 / 可变 → 审查层

    THINK_PRE --> OBSERVE: ToolResult

    REVIEW --> AUTO_EXEC: 只读/白名单 → 直接执行
    REVIEW --> APPROVAL: 高风险 → pending_approval 返回

    APPROVAL --> WAIT: yield approval_required SSE
    WAIT --> RESUME: POST /api/tool-requests/{id}/approval
    RESUME --> EXEC: 编排器合并决策 → 重新调用图

    AUTO_EXEC --> OBSERVE: ToolResult
    EXEC --> OBSERVE: ToolResult
    APPROVAL --> REJECT: 拒绝 → 记录审计
    REJECT --> OBSERVE: 拒绝通知

    OBSERVE --> AGENT_STATE: 回写消息 → 继续循环
```

### 图拓扑（简图）

```mermaid
flowchart LR
    think["think<br/>LLM 推理"]
    review["review<br/>审查层"]
    act["act<br/>工具执行"]
    observe["observe<br/>处理结果"]
    think -- "只读预执行" --> observe
    think -- "写/可变" --> review
    think -- "已批准工具" --> act
    review --> act
    act --> observe
    observe --> think
    think -- "无输出" --> done["END"]
```

### 审批如何处理

Graph 是纯函数，不内部管理审批状态。`review_node` 返回 `pending_approval` 列表和 `Transition.APPROVAL_PENDING`，图执行结束。编排器（LoopOrchestrator）检测到 `pending_approval` 后：推送 `tool_approval_required` SSE 事件，通过 `ApprovalBridge` 等待用户决策，将决策合并到状态（`approved_tool_calls` / `rejected_tool_calls`），然后重新调用 `graph.ainvoke()`。`think_node` 检测到 `approved_tool_calls` 存在时走快速路径（跳过 LLM），直接路由到 `act_node` 执行。

### 循环状态

每个节点在返回值中附带 `transition` 字段，记录状态变更原因。编排器在每轮迭代结束时清理瞬态字段（`tool_calls`、`approved_tool_calls`、`pending_approval` 等）。

```mermaid
graph LR
    node_user["user_message"] --> node_think["think: LLM 推理"]
    node_think -- "只读预执行" --> node_tools["tool_results"]
    node_think -- "写/可变" --> node_review["review: 审查层"]
    node_review --> node_approval["approval_pending<br/>(编排器接管)"]
    node_review --> node_act["act: 工具执行"]
    node_approval --> node_granted["approval_granted<br/>(编排器合并决策)"]
    node_approval --> node_rejected["approval_rejected"]
    node_granted --> node_think
    node_act --> node_tools
    node_tools --> node_think
    node_think --> node_done["done"]
    node_think --> context_compacted["context_compacted"]
    context_compacted --> node_think
    node_think --> error_exit["error_exit"]
```

transition 值同时写入 `audit_events` 表（level=INFO），按 `chat_id` 可追溯完整循环轨迹。


---

## Agent State 内部结构

Agent State 是循环各阶段共享的上下文容器。每轮迭代从 State 读取消息和工具注册表，组装 prompt 输出到 LLM。

```mermaid
flowchart TD
    %% ── 写入端 ──
    write_init["初始消息<br/>来自 Session + Prompt Manager"]
    write_text["AssistantMessage<br/>LLM 文本回复"]
    write_toolcall["ToolCallMessage<br/>LLM 工具调用请求"]
    write_toolresult["ToolResult<br/>tool-server / rag-server 执行结果"]
    write_meta["Meta Message<br/>审批通过/拒绝/超时通知"]

    %% ── Agent State 内部 ──
    subgraph agent_state_internals["Agent State 内部"]
        msg_accumulator["Message Reducer<br/>追加新消息到消息列表"]
        msg_list[("消息列表<br/>user | assistant | tool_call | tool_result | system")]
        tool_registry[("工具注册表<br/>MCP 工具元数据 + paramsSchema")]

        token_counter{"Token Counter<br/>当前上下文 token 数"}
        threshold_check{"超安全阈值？"}
        truncator["Tool Result Truncation<br/>截断超长 tool_result<br/>只保留关键字段"]
        retry_check{"仍超阈值？"}
        summarizer["Summarizer<br/>调轻量 LLM 对早期消息<br/>生成摘要替换原文"]
        context_assembler["Context Assembler<br/>按 section 组装 system prompt<br/>+ 消息列表（含摘要）"]
        output_context["输出到 LLM<br/>完整上下文"]
    end

    %% ── 读取端 ──
    read_think["LLM 推理"]
    read_decide["终止判断<br/>LLM 是否结束循环"]

    %% 连线
    write_init --> msg_accumulator
    write_text --> msg_accumulator
    write_toolcall --> msg_accumulator
    write_toolresult --> msg_accumulator
    write_meta --> msg_accumulator

    msg_accumulator --> msg_list
    msg_list --> token_counter
    token_counter --> threshold_check
    threshold_check -- "否" --> context_assembler
    threshold_check -- "是" --> truncator
    truncator -- "截断后消息列表" --> retry_check
    retry_check -- "否" --> context_assembler
    retry_check -- "是" --> summarizer
    summarizer -- "压缩后的消息列表" --> msg_list

    msg_list --> context_assembler
    tool_registry --> context_assembler
    context_assembler --> output_context
    output_context --> read_think
    output_context --> read_decide
```

> **关键行为**：
> - **Message Reducer**：每次节点返回新消息时追加，不覆盖已有消息。
> - **Token Counter**：每轮 LLM 调用前以模型 tokenizer 计数。
> - **Tool Result Truncation**（第 1 层）：超阈值时先截断超长 tool_result，只保留摘要字段。开销几乎为零。
> - **Summarizer**（第 2 层）：截断后仍超阈值时，调轻量 LLM 对早期消息生成摘要；近期消息和工具结果优先保留原始内容。
> - **Context Assembler**：按 section 顺序（identity → rules → tool_usage → environment → memory）组装 system prompt，再拼接消息列表和可用工具定义，形成完整 prompt。Section 结构见 `doc/详细设计.md`。

---

## 并发 tool_call 处理

```mermaid
flowchart TD
    %% 声明
    llm_multi_response["LLM 单次响应<br/>含 N 个 tool_call"]
    classify{"分类 tool_call"}
    readonly_group["只读工具组<br/>静态 is_read_only = true<br/>→ think 节点预执行"]
    highrisk_group["写/可变工具组<br/>→ 审查层"]
    parallel_exec["并行执行<br/>全部只读同时发起"]
    parallel_wait["等待全部只读完成"]
    serial_queue["串行排队<br/>高风险依次处理"]
    has_next{"还有待处理<br/>tool_call？"}
    push_approval["推送 tool_approval_required<br/>SSE 连接保持存活"]
    wait_user["等待用户审批"]
    execute_one["执行单个高风险工具"]
    merge_done["全部 tool_call 完成<br/>继续 SSE 流"]

    %% 连线
    llm_multi_response --> classify
    classify -- "只读" --> readonly_group
    classify -- "高风险" --> highrisk_group
    readonly_group --> parallel_exec
    parallel_exec --> parallel_wait
    highrisk_group --> serial_queue
    serial_queue --> has_next
    has_next -- "是" --> push_approval
    push_approval --> wait_user
    wait_user --> execute_one
    execute_one --> has_next
    parallel_wait --> merge_done
    has_next -- "否" --> merge_done
```

---

## 错误处理与恢复链

错误类型对应 `doc/详细设计.md` 定义的 8 种标准化分类。可恢复错误在流式输出阶段**留置**（不推送 SSE error 事件），进入恢复路径。恢复成功 → 继续流，用户无感知；恢复失败 → 才推送 `error` 事件。不可恢复类型直接表面错误。

```mermaid
flowchart TD
    llm_error{"LLM Adapter<br/>error_type？"}

    %% prompt_too_long 分支
    ptl["prompt_too_long<br/>（留置：不推送 error）"]
    compress_compact{"上下文压缩<br/>截断→摘要？"}
    compact_ok["恢复成功<br/>清理流式中间状态<br/>重试 API 调用<br/>（用户无感知）"]
    compress_aggressive{"更激进压缩？"}
    aggressive_ok["恢复成功<br/>清理流式中间状态<br/>yield compact boundary<br/>重试 API 调用<br/>（用户无感知）"]
    ptl_surface["恢复链耗尽<br/>yield error 事件<br/>记录 llm_traces.error_type"]

    %% max_output_tokens 分支
    mot["max_output_tokens<br/>（留置：不推送 error）"]
    mot_escalate{"首次且可提升？"}
    mot_escalate_ok["恢复成功<br/>清理流式中间状态<br/>提升到 64K 上限<br/>重试 API 调用<br/>（用户无感知）"]
    mot_recovery{"恢复次数 < 3？"}
    mot_inject["恢复成功<br/>清理流式中间状态<br/>注入 'continue' meta message<br/>创建新回合<br/>（用户无感知）"]
    mot_surface["恢复链耗尽<br/>yield error 事件<br/>记录 llm_traces.error_type"]

    %% 不可恢复类型
    other["rate_limit<br/>auth_failed<br/>model_unavailable<br/>server_error<br/>timeout<br/>unknown<br/>（不可恢复，直接展示）"]
    other_surface["yield error 事件<br/>记录 llm_traces.error_type"]

    audit["记录 audit_events<br/>LLM_RATE_LIMITED / LLM_AUTH_FAILED /<br/>LLM_MODEL_UNAVAILABLE / LLM_TIMEOUT /<br/>LLM_PROMPT_TOO_LONG / LLM_CALL_FAILED"]
    exit_loop["直接退出循环<br/>不回到 agent_state<br/>不触发新一轮 LLM 推理"]

    llm_error -- "prompt_too_long" --> ptl
    llm_error -- "max_output_tokens" --> mot
    llm_error -- "其他不可恢复<br/>rate_limit / auth_failed /<br/>model_unavailable / server_error /<br/>timeout / unknown" --> other

    ptl --> compress_compact
    compress_compact -- "是" --> compact_ok
    compress_compact -- "否" --> compress_aggressive
    compress_aggressive -- "是" --> aggressive_ok
    compress_aggressive -- "否" --> ptl_surface

    mot --> mot_escalate
    mot_escalate -- "是" --> mot_escalate_ok
    mot_escalate -- "否" --> mot_recovery
    mot_recovery -- "是" --> mot_inject
    mot_recovery -- "否(≥3次)" --> mot_surface

    other --> other_surface

    ptl_surface --> audit
    mot_surface --> audit
    other_surface --> audit

    audit --> exit_loop
```

### 恢复策略

可恢复错误在流式阶段留置（不推送 SSE `error`），进入恢复链。不可恢复类型直接表面错误。每层恢复有 guard 变量防止重入——同一层不会因为重试后仍失败而再次触发同一策略。

| 错误类型 | 分类 | SSE 行为 | 第1层 | 第2层 | 第3层 | 防重入 guard |
|----------|------|----------|-------|-------|-------|-------------|
| prompt_too_long | 可恢复 | 留置，恢复成功则继续 | 上下文压缩（截断→摘要），重试 | 更激进的压缩策略，重试 | 终止并展示错误 | 第1层：`has_compacted_this_turn`；第2层：`has_attempted_aggressive_compact`；新一轮重置 |
| max_output_tokens | 可恢复 | 留置，恢复成功则继续 | 提升 token 上限透明重试 | 注入 "continue" 消息，最多 3 次 | 终止并展示错误 | 第1层：`max_tokens_escalated`；第2层：`output_token_recovery_count < 3` |
| rate_limit | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |
| model_unavailable | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |
| server_error | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |
| auth_failed | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |
| timeout | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |
| unknown | 不可恢复 | 立即推送 error | 终止并展示错误 | — | — | — |

### 重试前的状态清理

每次恢复重试 LLM 调用前，清理上一轮流式阶段累积的中间状态：
- **tombstone 上一轮的 assistant 消息**（向 UI 发送移除标记）
- 清空临时累加器（`assistant_messages`、`tool_results`、`tool_use_blocks`）
- 丢弃流式工具执行器的待处理任务，创建新实例

这避免了 orphaned tool_use/tool_result 序列污染重试请求，导致 API 报错。

### 死亡螺旋预防

以下三条规则防止"错误 → 重试 → 错误"的无限循环：

| 规则 | 说明 |
|------|------|
| 终止并展示错误后直接退出 | LLM 调用不可恢复错误或恢复链耗尽后，循环直接进入 `exit_loop`，不回到 `agent_state`。此时 LLM 未产出有效回复，继续推理无意义 |

---

## 会话生命周期

```mermaid
flowchart LR
    %% 声明
    create_session["POST /api/sessions<br/>创建会话"]
    active["会话活跃<br/>Agent ReAct 循环中"]
    persist[("PostgreSQL 持久化<br/>chat_sessions / messages / tool_calls")]
    restore["重启后恢复<br/>加载历史消息"]
    expire_pending["重启时审批失效<br/>（内存态，不持久化）"]

    %% 连线
    create_session --> active
    active --> persist
    persist --> restore
    restore --> active
    active -- "服务重启" --> expire_pending
```

---

## 类图

展示组件静态结构：各层有哪些类、类之间的依赖和继承关系、每个类对外暴露的核心职责。

```mermaid
classDiagram
    %% ============================================================
    %% 外部框架/库
    %% ============================================================
    namespace external {
        class FastAPI_APIRouter {
            <<framework>>
        }
        class fastmcp_Client {
            <<library>>
        }
        class OpenAI_Client {
            <<library>>
        }
        class PostgreSQL {
            <<database>>
        }
    }

    %% ============================================================
    %% models — 数据模型，对应 doc/api-spec/openapi.yaml 的 schemas
    %% ============================================================
    namespace models {
        class Message {
            +messageID
            +chat_id
            +type: user|assistant|tool_call|tool_result|system
            +content
            +isMeta
        }
        class Tool {
            +name
            +server_name
            +description
            +mutable
            +is_read_only
            +is_rollbackable
        }
        class ToolCall {
            +chat_id
            +messageID
            +params
            +approval_status
            +execution_status
        }
        class ToolRequest {
            +request_id
            +approval_status: PENDING|APPROVED|REJECTED|EXPIRED
        }
        class ToolApproval {
            +approval_status: APPROVED|REJECTED
            +reason
        }
        class ToolResult {
            +execution_status: SUCCEEDED|FAILED
            +output
            +error
        }
        class ChatSession {
            +chat_id
            +messages[]
            +executed_tool_list[]
        }
        class AuditEvent {
            +level: INFO|WARN|ERROR|CRITICAL
            +event
            +actor
            +decision
        }
    }

    %% ============================================================
    %% agent — 编排层
    %% ============================================================
    namespace agent {
        class AgentState {
            +messages[]
            +available_tools[]
            +system
            +transition
        }
        class Query {
            +run(messages, tools, system) AsyncGenerator[SSEEvent]
            +resume(decisions) AsyncGenerator[SSEEvent]
        }
        class LoopOrchestrator {
            +run(initial_state) AsyncIterator
        }
    }

    %% ============================================================
    %% security — 审查层
    %% ============================================================
    namespace security {
        class RuleEngine {
            +evaluate(tool_name, is_read_only, is_rollbackable) str
        }
        class PendingApprovalBridge {
            +register(request_id, chat_id)
            +get_chat_id(request_id) str
            +remove(request_id)
        }
    }

    %% ============================================================
    %% mcp_client
    %% ============================================================
    namespace mcp_client {
        class ToolExecutor {
            +execute(tool_call) ToolResult
            +execute_parallel(tool_calls[]) ToolResult[]
            +list_tools() Tool[]
        }
    }

    %% ============================================================
    %% services
    %% ============================================================
    namespace services {
        class SessionManager {
            +create_session() ChatSession
            +get_session(chat_id) ChatSession
            +add_message(chat_id, msg)
        }
        class PromptManager {
            +load_sections() dict
            +build_system_prompt() str
        }
        class LLMAdapter {
            +generate(messages, tools) LLMResponse
            +generate_stream(messages, tools) AsyncIterator
        }
        class ContextManager {
            +count_tokens(messages) int
            +compress(messages) Message[]
        }
    }

    %% ============================================================
    %% observability
    %% ============================================================
    namespace observability {
        class AuditLogger {
            +log(event: AuditEvent)
        }
        class Tracer {
            +trace_llm_call(chat_id, model, usage, latency_ms)
        }
    }

    %% ============================================================
    %% api — FastAPI Router
    %% ============================================================
    namespace api {
        class ChatRouter
        class SessionRouter
        class ApprovalRouter
        class HealthRouter
    }

    %% ============================================================
    %% config
    %% ============================================================
    namespace config {
        class WebServerConfig
        class RulesConfig
        class LLMConfig
        class ServersConfig
    }

    %% ============================================================
    %% 关系：自定义 → 外部框架
    %% ============================================================
    ToolExecutor --> fastmcp_Client : wraps
    LLMAdapter --> OpenAI_Client : uses
    LLMAdapter --> PostgreSQL : traces to
    SessionManager --> PostgreSQL : uses

    %% ============================================================
    %% 关系：自定义内部
    %% ============================================================
    ToolCall --> ToolResult : produces

    ChatRouter --> Query : invokes
    SessionRouter --> SessionManager : invokes
    ApprovalRouter --> PendingApprovalBridge : uses

    Query --> LoopOrchestrator : creates
    LoopOrchestrator --> AgentState : drives
    AgentState --> ContextManager : uses
    AgentState "1" --> "*" Message : accumulates
    AgentState "1" --> "*" Tool : registers

    RuleEngine --> RulesConfig : reads
    PendingApprovalBridge --> AuditLogger : writes
    RuleEngine --> AuditLogger : writes

    SessionManager "1" --> "*" ChatSession : persists
    ChatSession "1" --> "*" Message : contains
    ChatSession "1" --> "*" ToolCall : contains
    PromptManager --> AgentState : injects

    WebServerConfig --> LLMAdapter : injects
    WebServerConfig --> RuleEngine : injects
    WebServerConfig --> AuditLogger : injects
    WebServerConfig --> Tracer : injects
```

---

## 数据流图

### 全系统数据流：用户输入到 SSE 响应

```mermaid
flowchart TD
    %% ============================================================
    %% Layer 1: Frontend (Browser)
    %% ============================================================
    subgraph frontend["前端 (Vue 3)"]
        direction TB
        chat_view["ChatView.vue<br/>时间线渲染"]
        msg_item["MessageItem.vue<br/>Markdown 消息"]
        tc_inline["ToolCallInline.vue<br/>内联工具调用文本<br/>tool_name / params / 状态 / 耗时"]
        reason_bubble["ReasoningBubble.vue<br/>折叠 / 展开<br/>灰色小号文本"]
        status_bar["状态栏<br/>Thinking… / Calling tool… / Responding…"]
        approval_inline["ApprovalInline.vue<br/>审批内联卡片<br/>timeline 内嵌"]
        error_banner[".connection-error 横幅<br/>错误提示"]
        session_mgr["useSessionManager<br/>per-session state<br/>messages / toolCalls / reasonings / SSE"]
    end

    subgraph web_server["Web-server (FastAPI :11450)"]
        direction TB
        api_chat["POST /api/chat<br/>SSE 入口"]
        api_approval["POST /api/tool-requests/{id}/approval<br/>审批回调"]

        subgraph agent_loop["Agent 循环 (LangGraph StateGraph)"]
            direction TB
            think["think_node<br/>LLM 流式推理<br/>accumulate text / reasoning / tool_calls"]
            dispatch["_dispatch_tool_calls<br/>三池分配"]
            pre_exec["安全池预执行<br/>execute_parallel()"]
            review["review_node<br/>classify_companion<br/>→ rule_engine.evaluate"]
            pending_approval["pending_approval<br/>编排器接管审批"]
            act["act_node<br/>execute_parallel()"]
            observe["observe_node<br/>合并 tool_results<br/>→ tool 消息"]
        end

        subgraph events["SSE 事件发射"]
            emit["emit_events()"]
        end

        subgraph reasoning_channel["推理侧通道 (B2a)"]
            ctx_var["contextvar _event_queue"]
            reasoning_q["asyncio.Queue(maxsize=64)<br/>背压控制"]
            drain_task["后台 drain_task<br/>持续读出 → buffered"]
        end
    end

    subgraph external["外部依赖"]
        llm["LLM (DeepSeek V4)<br/>流式 SSE"]
        tool_server["tool-server (:11451)<br/>fastmcp MCP"]
    end

    user_input["用户输入"] --> chat_view
    chat_view --> session_mgr
    session_mgr -- "POST /api/chat" --> api_chat

    api_chat --> svc["Session Manager /<br/>Prompt Manager"]
    svc --> think

    think <--> llm
    llm -- "reasoning_content" --> ctx_var
    ctx_var --> reasoning_q
    reasoning_q --> drain_task
    drain_task -- "buffered[]" --> emit
    emit -- "event: reasoning" --> session_mgr
    session_mgr --> reason_bubble
    reason_bubble --> chat_view

    llm -- "assistant content" --> think
    think -- "accumulated text" --> emit
    emit -- "event: assistant" --> session_mgr
    session_mgr --> msg_item
    msg_item --> chat_view

    llm -- "tool_use blocks" --> think
    think -- "tool_call_blocks" --> dispatch

    dispatch -- "安全池 (readonly)" --> pre_exec
    dispatch -- "审批池 (non-mutable write)" --> review
    dispatch -- "动态池 (mutable)" --> review

    pre_exec --> tool_server
    pre_exec -- "streaming_tool_results" --> emit
    emit -- "event: tool_call + tool_result" --> session_mgr

    review --> tool_server
    review -- "高风险" --> pending_approval
    pending_approval -- "event: tool_approval_required<br/>SSE 连接保持存活" --> emit
    emit --> session_mgr
    session_mgr --> approval_inline
    approval_inline -- "POST /approval" --> api_approval
    api_approval -- "编排器合并决策 → 重新调用图" --> act

    review -- "自动批准" --> act
    act --> tool_server
    act -- "tool_results" --> observe
    observe -- "tool 消息" --> emit
    emit -- "event: tool_result" --> session_mgr
    session_mgr --> tc_inline

    observe --> think

    emit -- "event: done" --> session_mgr

    session_mgr -- "error" --> error_banner
    session_mgr -- "agentPhase / phaseLabel" --> status_bar
```

### 用户消息 → SSE 事件序列图

```mermaid
sequenceDiagram
    participant U as Browser
    participant CV as ChatView.vue
    participant SM as useSessionManager
    participant WS as web-server /api/chat
    participant Q as asyncio.Queue
    participant DT as drain_task
    participant TH as think_node
    participant LLM as DeepSeek V4
    participant EM as emit_events

    U->>CV: 输入文字 / 点击发送
    CV->>SM: state.sendMessage(text)
    SM->>WS: POST /api/chat (SSE)

    Note over WS: 创建 Queue, set contextvar
    Note over WS: 启动 drain_task (后台)

    WS->>TH: graph.ainvoke()

    Note over TH,LLM: ====== LLM 流式阶段 ======

    TH->>LLM: generate_stream(messages, tools)
    LLM-->>TH: delta {content, reasoning_content}
    Note over TH: 提取 reasoning
    TH->>Q: put({event: "reasoning", data: '{"delta":"..."}'})
    Q->>DT: get()
    DT->>DT: append to buffered[]

    LLM-->>TH: tool_call block
    TH->>TH: accumulate tool_call_blocks

    TH->>Q: put({event: "thinking_done"})
    Q->>DT: get() → break loop
    Note over TH: LLM 流式结束 → dispatch tools

    TH->>EM: 返回 messages, tool_calls

    Note over WS,EM: ====== 事件发射 ======

    loop 每个 buffered reasoning
        DT->>EM: buffered reasoning events
        EM-->>WS: reasoning event
        WS-->>SM: event: reasoning\ndata: {"delta":"..."}
        SM->>SM: on_reasoning → reasonings[]
        SM->>CV: ReasoningBubble live update
    end

    EM-->>WS: assistant event
    WS-->>SM: event: assistant\ndata: {"delta":"...","message_id":"..."}
    SM->>SM: on_assistant → messages[]
    SM->>CV: update messages[]

    EM-->>WS: reasoning event (batch done=true)
    WS-->>SM: event: reasoning\ndata: {"delta":"...","done":true}
    SM->>SM: dedup → mark existing done
    SM->>CV: reasoning bubble → "Done"

    EM-->>WS: tool_call event
    WS-->>SM: event: tool_call\ndata: {"tool_name":"get_cpu","server":"tool-server",...}
    SM->>SM: on_tool_call → toolCalls Map

    EM-->>WS: tool_result event
    WS-->>SM: event: tool_result\ndata: {"tool_name":"get_cpu","execution_time_ms":45}
    SM->>SM: on_tool_result → update ToolCallInfo

    EM-->>WS: done event
    WS-->>SM: event: done\ndata: {"chat_id":"..."}
    SM->>SM: on_done → isStreaming=false<br/>新 session: transitionDraftToReal
    CV->>CV: status bar disappears
```

### 前端状态管理 & 时间线合并

```mermaid
flowchart LR
    subgraph sse_in["SSE Event Stream"]
        R["event: reasoning<br/>delta / done"]
        A["event: assistant<br/>delta"]
        TC["event: tool_call<br/>tool_name / params / server"]
        TR["event: tool_result<br/>status / execution_time_ms"]
        AP["event: tool_approval_required<br/>request_id / tool_name"]
        D["event: done"]
    end

    subgraph handlers["SessionState SSE 回调"]
        onR["on_reasoning"]
        onA["on_assistant"]
        onTC["on_tool_call"]
        onTR["on_tool_result"]
        onAP["on_tool_approval_required"]
        onDone["on_done"]
        onErr["on_error → connectionError"]
    end

    subgraph state["SessionState 响应式状态"]
        MSG["messages: Ref&lt;Message[]&gt;"]
        TC_STATE["toolCalls: Ref&lt;Map&gt;"]
        REASON["reasonings: Ref&lt;ReasoningEntry[]&gt;"]
        PHASE["agentPhase: AgentPhase<br/>idle / thinking / calling_tool<br/>/ awaiting_approval / responding / done"]
        APEVT["approvalEvent: ApprovalEvent<br/>status: pending / approved / rejected"]
        LABEL["phaseLabel: ComputedRef<br/>Thinking… / Calling tool…<br/>/ Responding…"]
        ERR["connectionError: string|null<br/>错误横幅显示"]
    end

    subgraph timeline["ChatView timeline 合并"]
        items["TimelineItem[]<br/>按 ts 排序"]
        msg_t["type: message<br/>MessageItem.vue"]
        tc_t["type: tool_call<br/>ToolCallInline.vue"]
        reason_t["type: reasoning<br/>ReasoningBubble.vue"]
        ap_t["type: approval<br/>ApprovalInline.vue"]
    end

    R --> onR
    A --> onA
    TC --> onTC
    TR --> onTR
    AP --> onAP
    D --> onDone

    onR --> REASON
    onA --> MSG
    onR --> PHASE
    onA --> PHASE
    onTC --> TC_STATE
    onTC --> PHASE
    onTR --> TC_STATE
    onAP --> APEVT
    onAP --> PHASE
    onDone --> PHASE
    onErr --> ERR

    MSG --> items
    TC_STATE --> items
    REASON --> items
    PHASE --> LABEL

    msg_t --> items
    tc_t --> items
    reason_t --> items
    ap_t --> items

    APEVT --> ap_t
```

### 三池工具分配

```mermaid
flowchart TD
    llm_out["LLM 单次响应<br/>N 个 tool_use block"]
    dispatch["_dispatch_tool_calls"]

    subgraph meta_check["元数据分类"]
        mutable{"meta.mutable?"}
        readonly{"meta.is_read_only?"}
    end

    subgraph safe_pool["安全池"]
        pre_exec["execute_parallel()<br/>全部并行"]
        pre_result["streaming_tool_results[]<br/>含 execution_time_ms"]
    end

    subgraph approval_pool["审批池"]
        classify["classify_companion()"]
        rule_eval["rule_engine.evaluate()"]
        rule_auto["AUTO_APPROVE"]
        rule_pending["NEEDS_APPROVAL"]
        pending_wait["编排器等待审批<br/>SSE 保持连接"]
    end

    subgraph act_exec["执行 (act_node)"]
        act_exec_par["execute_parallel()<br/>记录耗时"]
        act_result["tool_results[]<br/>含 execution_time_ms"]
    end

    llm_out --> dispatch
    dispatch --> meta_check

    meta_check --> mutable
    mutable -- "True" --> classify
    mutable -- "False" --> readonly

    readonly -- "True" --> pre_exec
    readonly -- "False" --> classify

    pre_exec --> pre_result

    classify --> rule_eval
    rule_eval --> rule_auto
    rule_eval --> rule_pending

    rule_auto --> act_exec_par
    rule_pending --> pending_wait
    pending_wait --> act_exec_par

    act_exec_par --> act_result
```
