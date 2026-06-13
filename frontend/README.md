# frontend

对话式运维聊天 UI。

## 技术栈

| 依赖 | 用途 |
|------|------|
| Vue 3 + TypeScript | 组件 + 状态 |
| Bootstrap 5 | UI 样式 + 布局 |
| marked.js | Markdown → HTML |
| DOMPurify | HTML 防 XSS |
| highlight.js | 代码块语法高亮 |
| fetch-event-source | SSE 客户端 |
| Nginx | serve 静态文件 + 反向代理 `/api/*` 到 web-server |

Docker 镜像只含构建产物（`dist/`）。

## 目录结构

```
frontend/
  README.md
  Dockerfile
  nginx.conf
  vue-project/
    index.html
    vite.config.ts
    vitest.config.ts
    src/
      main.ts
      App.vue
      domain/
        models.ts           # Message, ToolCallInfo, ChatSession, AgentPhase…
        sse-events.ts       # SSE 事件 DTO + SSECallbacks
      application/
        ports.ts            # SseClient, SessionApi, ApprovalApi 接口
        chat-store.ts       # 单会话状态（messages, toolCalls, reasonings, agentPhase…）
        session-list-store.ts # 会话列表状态
        session-service.ts  # 会话 CRUD + 审批（调 API + 更新 store）
        toast-store.ts      # Toast 通知队列
      infrastructure/
        sse-client.ts       # fetch-event-source 封装
        session-api.ts      # Session REST API
        approval-api.ts     # Approval REST API
        markdown.ts         # marked + DOMPurify + highlight.js 渲染管线
      presentation/
        composables/
          use-chat.ts       # 注入 store + sseClient，接管 SSE 生命周期
          use-session-list.ts # 注入 store + service，暴露会话列表操作
          use-timeline.ts   # 合并 messages/toolCalls/reasonings/approval → 排序 timeline
          use-auto-scroll.ts # 自动滚底 + 用户上滚检测
          use-toast.ts      # 注入 toastStore
      components/
        ChatView.vue        # 聊天面板：timeline + 输入框 + 错误横幅 + 状态指示
        MessageItem.vue     # 消息气泡：user/assistant/system/meta 渲染
        ToolCallInline.vue  # 工具调用行：tool_name + params + 状态 + 耗时
        ReasoningBubble.vue # 推理气泡：可折叠 + 流式光标
        ApprovalInline.vue  # 审批卡片：内嵌 approve/reject + 可选消息
        SessionList.vue     # 侧边栏：会话列表 + 时间分组 + 右键删除
        ToastContainer.vue  # Toast 容器：右上角叠加
      assets/
        style.css           # 自定义样式
```

## 架构

4 层：

- **domain**: 纯数据类型。`models.ts`（Message, ToolCallInfo, ChatSession…）、`sse-events.ts`（wire 格式 DTO）
- **application**: 业务状态 + 编排。stores 是带 `Ref<>` 的 class，唯一管理状态；`SessionService` 编排多 store 操作
- **infrastructure**: IO。`SessionApi` / `ApprovalApi`（fetch REST）、`SseClient`（fetch-event-source）、`markdown.ts`（渲染管线）
- **presentation**: Vue 组件 + composables。composables 通过 `inject` 消费 infrastructure/application，组件只管渲染 + emit

### 状态

App.vue 创建单例 stores → `provide`。`activeChatStore` 用 `shallowRef<ChatStore>` 包装，交换 session 时替换整个 store。

```
App.vue (provide)
├── toastStore: ToastStore           # 全局 Toast
├── sessionListStore: SessionListStore # 会话列表
├── sessionService: SessionService   # 编排（CRUD + 审批）
├── chatStore: Ref<ChatStore>        # shallowRef — 切换 session 替换
└── sseClient: FetchEventSourceClient # 单例
```

### 数据流

```
用户输入 → ChatView → useChat.send(text)
  → POST /api/chat (EventSource)
  → on_session_init → 绑定 chatId
  → on_reasoning   → ChatStore.appendReasoningDelta
  → on_assistant   → ChatStore.append + on_assistant_done → addMessage
  → on_tool_call   → ChatStore.setToolCall (RUNNING)
  → on_tool_result → ChatStore.updateToolCall (SUCCEEDED/FAILED)
  → on_tool_approval_required → ChatStore.setApprovalEvent
  → on_done        → ChatStore.setPhase('done')
  → on_error       → ChatStore.setConnectionError
```

### Session 生命周期

1. 点击 "New Session" → 调 API 预创建 session → 侧边栏出现条目
2. 发消息 → useChat 建立 SSE → `on_session_init` 绑定 chatId → `on_done` 更新 chatId
3. 切换 session → App.vue 替换 `activeChatStore`（`shallowRef`） → useChat 响应式跟随新 store
4. 删除 session → 调 API DELETE → 本地移除 → 切回空白

### SSE 事件

| 事件 | data | 触发 |
|------|------|------|
| `session_init` | `{ chat_id }` | 连接建立 |
| `reasoning` | `{ delta }` | 推理 delta |
| `thinking_done` | `{}` | 推理结束 |
| `assistant` | `{ delta }` | 回复 delta |
| `assistant_done` | `{}` | 回复结束 |
| `tool_call` | `ToolCallEvent` | LLM 调用工具 |
| `tool_result` | `ToolResultEvent` | 工具执行完毕 |
| `tool_approval_required` | `ToolApprovalRequiredEvent` | 高风险工具等审批 |
| `done` | `{}` | SSE 流正常结束 |
| `error` | `{ code, message }` | 错误 |

### 审批

`on_tool_approval_required` → `ApprovalInline.vue` 卡片嵌入 timeline → 点 approve/reject → `POST /api/tool-requests/{id}/approval`

### 输入组件

App.vue、ChatView.vue、SessionList.vue、MessageItem.vue、ToolCallInline.vue、ReasoningBubble.vue、ApprovalInline.vue、ToastContainer.vue
