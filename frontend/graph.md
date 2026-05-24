# frontend 流程图

## 总览：用户交互全链路

```mermaid
flowchart TD
    %% ============================================================
    %% 1. 节点声明（Declaration）
    %% ============================================================

    %% ── 初始化 ──
    page_load["页面加载<br/>index.html"]
    vue_init["Vue 3 初始化<br/>createApp mount"]
    vendor_load["加载 vendor 库<br/>Bootstrap 5 + marked.js"]
    fetch_sessions["GET /api/sessions<br/>加载会话列表"]

    %% ── 会话管理 ──
    session_list["会话列表渲染<br/>侧边栏"]
    session_create["POST /api/sessions<br/>创建新会话"]
    session_switch["切换会话<br/>GET /api/sessions/{chat_id}"]
    session_history["加载历史消息<br/>messages + executed_tool_list"]

    %% ── 聊天输入 ──
    user_input["用户输入消息<br/>textarea + 发送按钮"]
    msg_display["消息列表渲染<br/>message list DOM"]
    sse_connect["建立 SSE 连接<br/>POST /api/chat-turn<br/>Accept: text/event-stream"]

    %% ── SSE 事件分发 ──
    sse_dispatcher{"SSE event<br/>类型分发"}

    %% ── assistant 分支 ──
    assistant_delta["接收 text delta<br/>增量追加到当前消息"]
    markdown_render["marked.js 渲染<br/>Markdown → HTML"]
    assistant_append["追加到消息列表 DOM<br/>流式打字效果"]

    %% ── tool_call 分支 ──
    toolcall_notify["显示工具调用通知<br/>tool_name + params"]
    toolcall_badge["消息列表插入工具卡片<br/>状态: 执行中"]

    %% ── tool_result 分支 ──
    toolresult_update["更新工具卡片状态<br/>SUCCEEDED / FAILED"]
    toolresult_render["渲染工具结果<br/>output / error"]

    %% ── tool_approval_required 分支 ──
    approval_modal_show["弹出审批弹窗<br/>tool_name + params + reason"]
    sse_pause["SSE 流暂停<br/>等待用户决策"]

    %% ── 审批决策 ──
    user_approve["用户点击 批准"]
    user_reject["用户点击 拒绝<br/>可选填写原因"]
    approval_post["POST /api/tool-requests/{request_id}/approval<br/>approval_status + reason"]
    sse_resume["SSE 流恢复<br/>继续接收后续事件"]
    approval_modal_close["关闭审批弹窗<br/>插入审批结果 Meta 消息"]

    %% ── error 分支 ──
    error_display["显示错误提示<br/>code + message"]

    %% ── done 分支 ──
    sse_close["关闭 SSE 连接<br/>本轮对话结束"]

    %% ── 消息列表最终状态 ──
    msg_list_final["消息列表完整渲染<br/>支持滚动浏览历史"]

    %% ============================================================
    %% 2. 逻辑连线（Connections）
    %% ============================================================

    page_load --> vue_init
    page_load --> vendor_load
    vue_init --> fetch_sessions
    fetch_sessions --> session_list

    session_list --> session_create
    session_list --> session_switch
    session_switch --> session_history
    session_history --> msg_display
    session_create --> user_input

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

    sse_dispatcher -- "event: tool_approval_required" --> approval_modal_show
    approval_modal_show --> sse_pause
    sse_pause --> user_approve
    sse_pause --> user_reject
    user_approve --> approval_post
    user_reject --> approval_post
    approval_post --> sse_resume
    approval_post --> approval_modal_close
    approval_modal_close --> msg_display

    sse_dispatcher -- "event: error" --> error_display
    error_display --> msg_display

    sse_dispatcher -- "event: done" --> sse_close
    sse_close --> msg_list_final
```

---

## SSE 事件处理流程

```mermaid
flowchart TD
    %% 声明
    sse_stream["EventSource<br/>POST /api/chat-turn"]
    event_parse{"SSE 事件解析<br/>event + data"}

    %% 6 种事件处理
    subgraph handler_assistant["assistant 处理"]
        as_delta["提取 delta 文本"]
        as_buf["追加到 message buffer"]
        as_md["marked.js parse → HTML"]
        as_dom["定位当前消息 DOM 节点<br/>innerHTML 替换"]
    end

    subgraph handler_toolcall["tool_call 处理"]
        tc_create["创建工具卡片 DOM"]
        tc_icon["根据 isReadOnly 设图标<br/>🔍 只读 / ⚡ 高风险"]
        tc_status["状态标签: 执行中..."]
        tc_insert["插入消息列表"]
    end

    subgraph handler_toolresult["tool_result 处理"]
        tr_find["按 message_id 定位工具卡片"]
        tr_update["更新状态标签<br/>✅ SUCCEEDED / ❌ FAILED"]
        tr_output["折叠/展开 output 详情"]
    end

    subgraph handler_approval["tool_approval_required 处理"]
        ha_show["显示审批弹窗 overlay"]
        ha_info["渲染 tool_name + params + reason"]
        ha_btn["启用 批准/拒绝 按钮"]
        ha_pause["标记 SSE 流暂停"]
    end

    subgraph handler_error["error 处理"]
        he_toast["toast 提示<br/>code + message"]
        he_retry{"可恢复？"}
        he_close["关闭 SSE 连接"]
    end

    subgraph handler_done["done 处理"]
        hd_close["关闭 EventSource"]
        hd_input["启用输入框<br/>允许下一轮对话"]
    end

    %% 连线
    sse_stream --> event_parse
    event_parse -- "assistant" --> handler_assistant
    event_parse -- "tool_call" --> handler_toolcall
    event_parse -- "tool_result" --> handler_toolresult
    event_parse -- "tool_approval_required" --> handler_approval
    event_parse -- "error" --> handler_error
    event_parse -- "done" --> handler_done

    as_delta --> as_buf --> as_md --> as_dom
    tc_create --> tc_status --> tc_insert
    tr_find --> tr_update --> tr_output
    ha_show --> ha_info --> ha_btn --> ha_pause
    he_toast --> he_retry
    he_retry -- "是" --> sse_stream
    he_retry -- "否" --> he_close
```

---

## 审批交互流程

```mermaid
stateDiagram-v2
    [*] --> IDLE: 初始状态

    IDLE --> MODAL_SHOWN: SSE: tool_approval_required
    MODAL_SHOWN --> WAITING: 弹窗渲染完成<br/>流暂停

    WAITING --> APPROVING: 用户点击 批准
    WAITING --> REJECTING: 用户点击 拒绝

    APPROVING --> POSTING: POST /api/tool-requests/{id}/approval<br/>{approval_status: APPROVED}
    REJECTING --> POSTING: POST /api/tool-requests/{id}/approval<br/>{approval_status: REJECTED, reason}

    POSTING --> SUCCESS: 200 OK
    POSTING --> FAILED: 404 / 网络错误

    SUCCESS --> MODAL_CLOSED: 关闭弹窗<br/>流恢复
    FAILED --> MODAL_SHOWN: 显示错误<br/>允许重试

    MODAL_CLOSED --> IDLE: 等待下一轮事件

    WAITING --> EXPIRED: SSE 超时断开
    EXPIRED --> IDLE: 弹窗自动关闭<br/>显示超时提示
```

---

## 模块依赖图

```mermaid
flowchart LR
    %% 声明
    subgraph entry["入口"]
        index["index.html<br/>Alpine.js 单页应用"]
    end

    subgraph vendor["第三方库"]
        vue["Vue 3<br/>组件化框架"]
        bootstrap["Bootstrap 5<br/>UI 样式"]
        marked["marked.js<br/>Markdown 渲染"]
    end

    subgraph composables["Composables"]
        useSSE["useSSE.ts<br/>SSE 连接"]
        useChat["useChat.ts<br/>聊天状态管理"]
        useSessions["useSessions.ts<br/>会话管理"]
    end

    subgraph components["Vue 组件"]
        chatview["ChatView.vue<br/>聊天界面 + SSE"]
        approval_modal["ApprovalModal.vue<br/>审批弹窗"]
        toolcall_card["ToolCallCard.vue<br/>工具执行卡片"]
        message_item["MessageItem.vue<br/>消息渲染"]
        session_list["SessionList.vue<br/>会话侧边栏"]
        toast_container["ToastContainer.vue<br/>Toast 通知"]
    end

    subgraph external["外部依赖"]
        nginx["Nginx<br/>静态文件 serve<br/>+ /api/* 反代"]
        webapi["web-server<br/>OpenAPI 端点"]
    end

    %% 连线
    index --> vue
    index --> bootstrap
    index --> marked
    index --> chatview
    index --> approval_modal
    index --> session_list

    chatview --> message_item: 消息渲染
    chatview --> toolcall_card: 工具卡片渲染
    chatview --> useSSE: SSE 连接
    chatview --> webapi: fetch + ReadableStream
    chatview --> webapi: GET/POST /api/sessions

    approval_modal --> webapi: POST /api/tool-requests/{id}/approval

    useSSE --> webapi: POST /api/chat-turn (SSE)
    useChat --> useSSE: SSE events
    useChat --> useSessions: active chat ID

    toast_container --> useToast: toast 队列

    nginx --> index: serve 静态文件
    nginx --> webapi: 反向代理 /api/*
```

---

## Vue 组件树与数据流

```mermaid
classDiagram
    class App {
        +string current_chat_id
        +ChatSession[] sessions
        +Message[] messages
        +Map~string,ToolCallInfo~ toolCalls
        +bool is_streaming
        +ApprovalPending approvalPending
    }

    class ChatView {
        +string input_text
        +string assistant_buf
        +computed timeline
        +emit send_message, abort
        +props messages, toolCalls, is_streaming
    }

    class MessageItem {
        +props message: Message
        +computed rendered_html
    }

    class ToolCallCard {
        +props toolCall: ToolCallInfo
        +bool output_expanded
    }

    class ApprovalModal {
        +props visible, tool_name, params, reason, request_id
        +bool is_processing
        +emit approve, reject
    }

    class SessionList {
        +props sessions, active_chat_id, is_loading, is_creating
        +emit select, create
    }

    class ToastContainer {
        +Toast[] toasts
        +dismiss(id)
    }

    App --> ChatView
    App --> SessionList
    App --> ApprovalModal
    App --> ToastContainer
    ChatView --> MessageItem
    ChatView --> ToolCallCard
```

---

## 消息渲染管道

```mermaid
flowchart LR
    %% 声明
    msg_raw["SSE 原始数据<br/>event + data JSON"]
    classify{"消息类型？"}

    subgraph pipe_user["user 消息"]
        u_align["右对齐气泡<br/>用户头像"]
        u_text["纯文本<br/>不渲染 Markdown"]
    end

    subgraph pipe_assistant["assistant 消息"]
        a_buf["增量拼接到 buffer"]
        a_parse["marked.parse(buffer)"]
        a_dom["更新 DOM innerHTML"]
    end

    subgraph pipe_toolcall["tool_call 消息"]
        t_card["生成工具卡片<br/>标注 isReadOnly 状态"]
        t_status["状态: 执行中..."]
    end

    subgraph pipe_toolresult["tool_result 消息"]
        r_find["找到对应工具卡片"]
        r_status["更新状态: SUCCEEDED/FAILED"]
        r_output["折叠输出详情"]
    end

    subgraph pipe_system["system 消息"]
        s_banner["居中系统提示条<br/>灰色背景"]
    end

    subgraph pipe_meta["isMeta 消息"]
        m_small["小字提示<br/>不参与对话排版"]
    end

    %% 连线
    msg_raw --> classify
    classify -- "user" --> pipe_user
    classify -- "assistant" --> pipe_assistant
    classify -- "tool_call" --> pipe_toolcall
    classify -- "tool_result" --> pipe_toolresult
    classify -- "system" --> pipe_system
    classify -- "isMeta=true" --> pipe_meta
```
