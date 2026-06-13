# frontend 流程图

## 总览：用户交互全链路

```mermaid
flowchart TD
    %% ============================================================
    %% 1. 节点声明
    %% ============================================================

    %% ── 初始化 ──
    page_load["页面加载<br/>index.html"]
    vue_init["Vue 3 初始化<br/>createApp mount"]
    vendor_load["加载 vendor 库<br/>Bootstrap 5 + marked.js"]
    fetch_sessions["GET /api/sessions<br/>加载会话列表"]

    %% ── 会话管理 ──
    session_list["会话列表渲染<br/>SessionList.vue 侧边栏"]
    session_draft["新建草稿<br/>createDraft() 本地<br/>activeChatId = null"]
    session_switch["切换会话<br/>GET /api/sessions/{chat_id}<br/>loadHistory()"]
    session_history["加载历史消息<br/>messages + executed_tool_list"]

    %% ── 聊天输入 ──
    user_input["用户输入消息<br/>textarea + 发送按钮"]
    msg_display["消息列表渲染<br/>timeline 合并排序"]
    sse_connect["建立 SSE 连接<br/>POST /api/chat<br/>Accept: text/event-stream"]

    %% ── SSE 事件分发 ──
    sse_dispatcher{"SSE event<br/>类型分发"}

    %% ── assistant 分支 ──
    assistant_delta["接收 text delta<br/>增量追加到当前消息"]
    markdown_render["marked.js 渲染<br/>Markdown → HTML"]
    assistant_append["追加到消息列表<br/>agentPhase = 'responding'"]

    %% ── tool_call 分支 ──
    toolcall_notify["ToolCallInline 渲染<br/>tool_name + params + 状态"]
    toolcall_badge["消息列表内联显示<br/>状态: Running…"]

    %% ── tool_result 分支 ──
    toolresult_update["更新 ToolCallInline 状态<br/>SUCCEEDED / FAILED"]
    toolresult_render["显示耗时 + 可折叠 output"]

    %% ── tool_approval_required 分支 ──
    approval_inline_show["ApprovalInline 卡片<br/>timeline 内嵌"]
    approval_wait["等待用户决策<br/>approvalEvent.status = 'pending'"]

    %% ── 审批决策 ──
    user_approve["用户点击 批准<br/>可选附带 message"]
    user_reject["用户点击 拒绝<br/>可选附带 message"]
    approval_post["POST /api/tool-requests/{request_id}/approval<br/>approval_status + reason"]
    approval_resolved["卡片状态更新<br/>approved / rejected"]

    %% ── error 分支 ──
    error_display[".connection-error 横幅<br/>code + message"]

    %% ── done 分支 ──
    sse_close["关闭 SSE 连接<br/>本轮对话结束<br/>新 session: 更新 activeChatId + 列表"]

    %% ── 消息列表最终状态 ──
    msg_list_final["消息列表完整渲染<br/>支持滚动浏览历史"]

    %% ============================================================
    %% 2. 逻辑连线
    %% ============================================================

    page_load --> vue_init
    page_load --> vendor_load
    vue_init --> fetch_sessions
    fetch_sessions --> session_list

    session_list --> session_draft
    session_list --> session_switch
    session_switch --> session_history
    session_history --> msg_display
    session_draft --> user_input

    user_input --> sse_connect
    sse_connect --> sse_dispatcher

    sse_dispatcher -- "event: assistant" --> assistant_delta
    assistant_delta --> markdown_render
    markdown_render --> assistant_append
    assistant_append --> msg_display

    sse_dispatcher -- "event: tool_call" --> toolcall_notify
    toolcall_notify --> toolcall_badge
    toolcall_badge --> msg_display

    sse_dispatcher -- "event: tool_result" --> toolresult_update
    toolresult_update --> toolresult_render
    toolresult_render --> msg_display

    sse_dispatcher -- "event: tool_approval_required" --> approval_inline_show
    approval_inline_show --> approval_wait
    approval_wait --> user_approve
    approval_wait --> user_reject
    user_approve --> approval_post
    user_reject --> approval_post
    approval_post --> approval_resolved
    approval_resolved --> msg_display

    sse_dispatcher -- "event: error" --> error_display
    error_display --> msg_display

    sse_dispatcher -- "event: done" --> sse_close
    sse_close --> msg_list_final
```

---

## SSE 事件处理流程

```mermaid
flowchart TD
    sse_stream["fetchEventSource<br/>POST /api/chat"]
    event_parse{"SSE 事件解析<br/>event + data"}

    subgraph handler_assistant["assistant 处理"]
        as_delta["提取 delta 文本"]
        as_buf["追加到 message buffer"]
        as_md["marked.js parse → HTML"]
        as_phase["agentPhase = 'responding'"]
    end

    subgraph handler_toolcall["tool_call 处理"]
        tc_create["创建 ToolCallInfo"]
        tc_map["插入 toolCalls Map"]
        tc_phase["agentPhase = 'calling_tool'<br/>currentActivity = tool_name"]
    end

    subgraph handler_toolresult["tool_result 处理"]
        tr_find["按 message_id 定位"]
        tr_update["更新执行状态<br/>SUCCEEDED / FAILED"]
        tr_elapsed["记录 execution_time_ms"]
    end

    subgraph handler_approval["tool_approval_required 处理"]
        ha_set["approvalEvent = { status: 'pending' }"]
        ha_phase["agentPhase = 'awaiting_approval'"]
        ha_card["ApprovalInline 渲染按钮"]
    end

    subgraph handler_error["error 处理"]
        he_banner["connectionError 设置<br/>.connection-error 横幅"]
        he_stream["isStreaming = false"]
    end

    subgraph handler_done["done 处理"]
        hd_phase["agentPhase = 'done'"]
        hd_stream["isStreaming = false"]
        hd_new{"新 session?"}
        hd_assign["_transitionDraftToReal()<br/>更新 activeChatId + sessions 列表"]
        hd_input["输入框保持可用"]
    end

    sse_stream --> event_parse
    event_parse -- "assistant" --> handler_assistant
    event_parse -- "tool_call" --> handler_toolcall
    event_parse -- "tool_result" --> handler_toolresult
    event_parse -- "tool_approval_required" --> handler_approval
    event_parse -- "error" --> handler_error
    event_parse -- "done" --> handler_done

    as_delta --> as_buf --> as_md --> as_phase
    tc_create --> tc_map --> tc_phase
    tr_find --> tr_update --> tr_elapsed
    ha_set --> ha_phase --> ha_card
    he_banner --> he_stream
    hd_phase --> hd_stream
    hd_stream --> hd_new
    hd_new -- "是" --> hd_assign --> hd_input
    hd_new -- "否" --> hd_input
```

---

## 审批交互流程

```mermaid
stateDiagram-v2
    [*] --> IDLE: 初始状态

    IDLE --> PENDING: SSE: tool_approval_required<br/>ApprovalInline 卡片渲染
    PENDING --> WAITING: approvalEvent.status = 'pending'<br/>显示按钮

    WAITING --> APPROVING: 用户点击 Approve
    WAITING --> REJECTING: 用户点击 Reject

    APPROVING --> POSTING: POST /api/tool-requests/{id}/approval<br/>{approval_status: APPROVED, reason}
    REJECTING --> POSTING: POST /api/tool-requests/{id}/approval<br/>{approval_status: REJECTED, reason}

    POSTING --> RESOLVED: 200 OK<br/>卡片状态更新
    POSTING --> FAILED: non-ok / 网络错误<br/>connectionError 设置

    RESOLVED --> IDLE: approvalEvent.status = 'approved' | 'rejected'<br/>agentPhase = 'thinking'

    WAITING --> EXPIRED: SSE 超时断开<br/>后端自动拒绝
    EXPIRED --> IDLE: connectionError 横幅显示

    FAILED --> WAITING: 允许重试
```

---

## 模块依赖图

```mermaid
flowchart LR
    subgraph entry["入口"]
        index["index.html<br/>Vue 3 单页应用"]
    end

    subgraph vendor["第三方库"]
        vue["Vue 3<br/>组件化框架"]
        bootstrap["Bootstrap 5<br/>UI 样式"]
        marked["marked.js<br/>Markdown 渲染"]
        highlightjs["highlight.js<br/>代码语法高亮"]
    end

    subgraph application["应用层"]
        chatstore["ChatStore<br/>per-session 状态<br/>messages / toolCalls / reasonings"]
        sessionliststore["SessionListStore<br/>会话列表 / activeChatId"]
        session_service["SessionService<br/>CRUD + 审批编排"]
    end

    subgraph presentation["表现层"]
        chatview["ChatView.vue<br/>时间线渲染 + 状态栏"]
        message_item["MessageItem.vue<br/>消息渲染"]
        toolcall_inline["ToolCallInline.vue<br/>工具调用内联文本"]
        reasoning_bubble["ReasoningBubble.vue<br/>推理过程气泡"]
        approval_inline["ApprovalInline.vue<br/>审批内联卡片"]
        session_list["SessionList.vue<br/>会话侧边栏"]
        toast_container["ToastContainer.vue<br/>Toast 通知"]
        usechat["useChat<br/>SSE 生命周期 + store 操作"]
        usetimeline["useTimeline<br/>合并排序 timeline"]
    end

    subgraph external["外部依赖"]
        nginx["Nginx<br/>静态文件 serve<br/>+ /api/* 反代"]
        webapi["web-server<br/>OpenAPI 端点"]
    end

    index --> vue
    index --> bootstrap
    index --> marked
    index --> highlightjs
    index --> chatview
    index --> session_list

    chatview --> message_item: 消息渲染
    chatview --> toolcall_inline: 工具调用渲染
    chatview --> reasoning_bubble: 推理渲染
    chatview --> approval_inline: 审批渲染
    chatview --> usechat

    session_list --> usechat: via inject
    session_list --> sessionliststore: via useSessionList

    approval_inline --> session_service: approve / reject

    usechat --> chatstore: state 读写
    usechat --> sessionliststore: 新增 session
    session_service --> chatstore
    session_service --> sessionliststore

    usechat --> webapi: POST /api/chat (SSE)
    session_service --> webapi: GET/POST/DELETE /api/sessions
    session_service --> webapi: POST /api/tool-requests/{id}/approval

    nginx --> index: serve 静态文件
    nginx --> webapi: 反向代理 /api/*
```

---

## Vue 组件树与数据流

```mermaid
classDiagram
    class ChatStore {
        +Ref~string|null~ chatId
        +Ref~Message[]~ messages
        +Ref~Map~ toolCalls
        +Ref~ReasoningEntry[]~ reasonings
        +Ref~boolean~ isStreaming
        +Ref~string~ draftInput
        +Ref~ApprovalEvent~ approvalEvent
        +Ref~AgentPhase~ agentPhase
        +ComputedRef~string~ phaseLabel
        +Ref~boolean~ isLoadingHistory
        +Ref~ErrorInfo~ connectionError
        +addMessage / addMessages
        +setToolCall / updateToolCall / setToolCalls
        +appendReasoningDelta / markReasoningDone
        +setPhase / setStreaming
        +setApprovalEvent / updateApprovalStatus
        +loadFromSession / reset
    }

    class SessionListStore {
        +Ref~ChatSession[]~ sessions
        +Ref~string|null~ activeChatId
        +Ref~boolean~ isLoadingSessions
        +Ref~string~ loadError
        +setSessions / addSession / removeSession
        +setActive / setLoading / setError
    }

    class ToastStore {
        +Ref~Toast[]~ toasts
        +show / dismiss
    }

    class SessionService {
        +createDraft() Promise~string|null~
        +loadSessions()
        +loadHistory(chatId, chatStore) Promise~boolean~
        +deleteSession(chatId)
        +approve / reject
    }

    class ChatView {
        +inject: chatStore, sseClient, sessionService
        +computed timeline (useTimeline)
        +scroll / send / abort
    }

    class MessageItem {
        +props message: Message
        +computed rendered_html
    }

    class ToolCallInline {
        +props tool_call: ToolCallInfo
    }

    class ApprovalInline {
        +props event: ApprovalEvent
        +emit approve, reject
    }

    class SessionList {
        +inject: chatStore, sessionListStore, sessionService
        +emit select, create
    }

    ChatView --> useChat: 注入 + 暴露状态
    ChatView --> useTimeline: 合并排序
    ChatView --> MessageItem
    ChatView --> ToolCallInline
    ChatView --> ApprovalInline
    ChatView --> ReasoningBubble
    useChat --> ChatStore: state 读写
    SessionService --> ChatStore: 操作
    SessionService --> SessionListStore: 操作
```

---

## 消息渲染管道

```mermaid
flowchart LR
    msg_raw["SSE 原始数据<br/>event + data JSON"]
    classify{"消息类型？"}

    subgraph pipe_user["user 消息"]
        u_align["右对齐气泡<br/>蓝色背景"]
        u_text["纯文本<br/>不渲染 Markdown"]
    end

    subgraph pipe_assistant["assistant 消息"]
        a_buf["增量拼接到 buffer"]
        a_parse["marked.parse(buffer)"]
        a_dom["更新 DOM innerHTML"]
    end

    subgraph pipe_toolcall["tool_call"]
        t_inline["ToolCallInline<br/>tool_name + params"]
        t_status["状态: Running… / Done / Failed"]
        t_elapsed["耗时"]
    end

    subgraph pipe_toolresult["tool_result"]
        r_find["按 message_id 更新"]
        r_status["更新状态"]
        r_output["可折叠 output"]
    end

    subgraph pipe_system["system 消息"]
        s_banner["居中系统提示条<br/>灰色背景"]
    end

    subgraph pipe_meta["isMeta 消息"]
        m_small["小字提示<br/>不参与对话排版"]
    end

    subgraph pipe_approval["approval 卡片"]
        ap_card["ApprovalInline<br/>tool_name + params + reason"]
        ap_btns["Approve / Reject 按钮"]
    end

    msg_raw --> classify
    classify -- "user" --> pipe_user
    classify -- "assistant" --> pipe_assistant
    classify -- "tool_call" --> pipe_toolcall
    classify -- "tool_result" --> pipe_toolresult
    classify -- "system" --> pipe_system
    classify -- "isMeta=true" --> pipe_meta
    classify -- "approval" --> pipe_approval
```
