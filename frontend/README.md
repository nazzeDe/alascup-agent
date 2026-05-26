# frontend

对话式运维聊天界面。支持同时多个 session 并行对话。本地开发构建，Docker 仅包含构建产物。

## 技术栈

| 依赖 | 用途 |
|------|------|
| Vue 3 | 组件化 UI 框架 |
| Bootstrap 5 | UI 样式与响应式布局 |
| marked.js | Markdown → HTML 渲染 |
| DOMPurify | HTML 清洗，防 XSS |
| highlight.js | 代码块语法高亮 |
| Nginx | 静态文件 serve + `/api/*` 反向代理 |

Docker 镜像仅包含构建产物，不含 Bun 和 node_modules。前端在本地构建，无需考虑 loongarch64 兼容。

## 目录结构

```
frontend/
  .dockerignore
  .gitignore
  Dockerfile
  nginx.conf
  README.md
  graph.md
  test.md
  vue-project/
    index.html          # 主页面入口
    vite.config.ts      # Vite 构建配置
    vitest.config.ts    # Vitest 测试配置
    tsconfig.json
    env.d.ts
    package.json
    public/
      favicon.ico
    src/
      main.ts           # 应用入口
      types.ts          # TypeScript 类型定义
      App.vue           # 根组件（布局 + SessionList + ChatView）
      assets/
        style.css       # Bootstrap 5 主题 + 自定义样式
      composables/
        useSessionManager.ts  # 统一会话管理（per-session state、SSE、审批、会话列表）
        useToast.ts           # Toast 通知队列
      components/
        ChatView.vue          # 聊天界面（timeline、输入框、状态栏、错误横幅）
        MessageItem.vue       # 单条消息渲染（user/assistant/system/meta + Markdown）
        ToolCallInline.vue    # 工具调用内联文本（tool_name + params + 状态 + 耗时）
        ReasoningBubble.vue   # 推理过程气泡（可折叠、流式动画）
        ApprovalInline.vue    # 审批内联卡片（timeline 内嵌、可附带消息）
        SessionList.vue       # 会话侧边栏（列表 + 时间分组 + 活动指示）
        ToastContainer.vue    # Toast 通知容器（右上角叠加）
    dist/               # Vite 构建产物（Docker 使用）
```

## 架构

### useSessionManager

唯一的全局 composable，管理所有 session 的完整生命周期：

```
useSessionManager
├── sessions: Ref<ChatSession[]>            # 会话列表
├── activeChatId: Ref<string | null>        # 当前活跃 session（null = 空白草稿）
├── isLoadingSessions / loadError           # 加载/错误状态
├── instances: Map<chatId, SessionState>    # 懒创建的 per-session state
│
└── SessionState
    ├── chatId: string | null              # 自身标识（null 表示尚未持久化）
    ├── messages / toolCalls / reasonings   # 聊天数据
    ├── agentPhase / currentActivity        # agent 状态（'thinking' | 'calling_tool' | ...）
    ├── phaseLabel                          # computed: "Thinking…" / "Calling: …"
    ├── approvalEvent                       # 审批事件（timeline 内嵌）
    ├── draftInput                          # 输入框草稿（切换 session 保留）
    ├── isStreaming                         # 每个 session 独立的流状态
    ├── connectionError                     # 连接错误消息（null = 正常）
    ├── isLoadingHistory                    # 历史消息加载中
    ├── sendMessage(text)                   # 发送消息 + 建立 SSE
    ├── abort()                             # 停止当前 SSE 连接
    ├── approve(requestId, message?)        # 批准（带错误处理）
    ├── reject(requestId, message?)         # 拒绝（带错误处理）
    └── loadHistory()                       # 加载历史消息
```

### ChatView

新接口只接收一个 prop：`chatId: string | null`。内部通过 `useSessionManager().get(chatId)` 获取一切状态。

### 设计决策

- **Session 按第一条消息创建**：点击 "New Session" 仅清空 ChatView（本地 draft），不发 HTTP 请求。发送第一条消息时，后端在 SSE `done` 事件中带回 `chatId`。
- **后台 session 保持连接**：切换 activeChatId 不 abort 其他 session 的 SSE。
- **审批降级为 timeline 内嵌**：不再弹全局 modal。审批事件以 `ApprovalInline.vue` 卡片形式出现在消息流中。卡片内可直接 approve/reject，附带可选消息。
- **Tool call 内联显示**：紧凑的单行文本（tool_name + params + 状态 + 耗时），可折叠展开输出。
- **Toast 过滤**：全局 Toast 带 `chatId` 字段，只渲染当前活跃 session 的 toast。
