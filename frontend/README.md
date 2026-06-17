# frontend

Alascup Agent 的 Vue 3 + TypeScript 前端。该子项目负责会话列表、聊天流式渲染、工具调用展示、审批操作、历史会话加载和前端测试。

## 运行命令

在 `frontend/vue-project` 下执行：

```bash
bun install
bun run dev
bun run test
bun run type-check
bun run build
bun run test:e2e
bun run test:e2e:live
```

`frontend/Dockerfile` 使用 nginx 托管 `vue-project/dist/`。构建镜像前需要先执行 `bun run build` 生成静态产物。

## 目录结构

```text
frontend/
  Dockerfile
  nginx.conf
  README.md
  graph.md
  vue-project/
    src/
      domain/          # 后端 wire DTO 与前端领域数据结构
      application/     # 状态、服务、SSE 解释器、时间线投影
      infrastructure/  # fetch/SSE/markdown 适配器
      presentation/    # Vue composables
      components/      # Vue 视图与组件
      assets/
      __tests__/       # Vitest 单元测试
    tests/e2e/         # mock 后端的 Playwright 流程
    tests/e2e-live/    # 连接真实后端的 Playwright 流程
```

## 详细设计

前端按四层组织。

| 层级 | 目录 | 职责 |
|------|------|------|
| Domain | `src/domain/` | 定义 `Message`、`ChatSession`、`ToolCallInfo`、`ApprovalEvent`、SSE 事件 payload 等数据结构 |
| Application | `src/application/` | 保存会话与聊天状态，解释 SSE 事件，投影时间线，封装会话/审批用例 |
| Infrastructure | `src/infrastructure/` | 调用 `/api/*`，解析 SSE，渲染安全 Markdown |
| Presentation | `src/presentation/`、`src/components/` | 组合式函数和 Vue 组件，负责 UI 交互与渲染 |

### 核心应用模块

- `ChatStore`：单个活动会话的状态容器，包含消息、工具调用、思考过程、审批事件、流式状态、错误和输入草稿。
- `SessionListStore`：会话列表状态，包含当前选中会话、加载状态和加载错误。
- `SessionService`：会话加载/删除与审批提交的应用服务。删除会话是 best-effort，后端失败时仍会清理本地列表。
- `ActiveSessionWorkspace`：管理当前活动 `ChatStore`，负责加载会话列表、选择历史会话、新建草稿会话、删除会话、接收服务端 `session_init`。
- `ChatStreamInterpreter`：解释单轮 `/api/chat` SSE 流，将事件转换为前端状态变化。
- `projectTimeline`：把 `messages`、`toolCalls`、`reasonings`、`approvalEvent` 合并成按时间排序的 UI 时间线。

### 聊天状态机

`AgentPhase` 的取值：

| 状态 | 含义 |
|------|------|
| `idle` | 空闲，可输入 |
| `thinking` | 已发送用户消息，等待/接收 reasoning 或工具后续推理 |
| `calling_tool` | 已收到 `tool_call`，工具正在运行 |
| `waiting_for_tool` | `calling_tool` 超过 800ms 后的等待态 |
| `awaiting_approval` | 收到高风险工具审批请求 |
| `responding` | 正在接收 assistant 文本 |
| `done` | 收到 `done`，本轮流结束 |

`useChat.send(text)` 会立即追加用户消息、清空上一轮 reasoning、进入 `thinking`，并创建 `AbortController`。如果当前 `chat_id` 为空，本轮被视为新会话；收到 `session_init` 后，`ActiveSessionWorkspace` 会把服务端会话 ID 写入当前 store，并用首条用户消息生成侧边栏标题。标题规则为前 30 个字符，超长追加 `...`。

### 时间线投影

UI 不直接按数组分别渲染消息和工具调用，而是由 `projectTimeline` 生成统一的 `TimelineItem[]`：

```text
messages + toolCalls + reasonings + approvalEvent
  -> 按 timestamp 升序
  -> 同一时间按 reasoning、message、tool_call、approval 排序
  -> ChatView 分派给 ReasoningBubble / MessageItem / ToolCallInline / ApprovalInline
```

历史会话加载时，`ChatStore.loadFromSession()` 会从 `executed_tool_list` 恢复工具调用，并把 assistant 消息上的 `reasoning_content` 转换为已完成的 `ReasoningEntry`。

## 前后端接口

前端只依赖相对路径 `/api/*`，开发态通过 Vite 访问，容器态由 nginx 反向代理到后端。

### REST

| 方法 | 路径 | 前端调用方 | 说明 |
|------|------|------------|------|
| `GET` | `/api/sessions` | `FetchSessionApi.listSessions()` | 获取会话列表 |
| `GET` | `/api/sessions/{chat_id}` | `FetchSessionApi.getSession()` | 获取单个会话详情 |
| `DELETE` | `/api/sessions/{chat_id}` | `FetchSessionApi.deleteSession()` | 删除会话 |
| `POST` | `/api/tool-requests/{request_id}/approval` | `FetchApprovalApi.approve/reject()` | 提交工具审批结果 |
| `POST` | `/api/chat` | `FetchEventSourceClient.connect()` | 发送用户消息并接收 SSE |

审批请求体：

```json
{ "approval_status": "APPROVED", "reason": "" }
```

拒绝请求体：

```json
{ "approval_status": "REJECTED", "reason": "" }
```

聊天请求体：

```json
{ "chat_id": "optional-existing-chat-id", "message": "用户输入" }
```

新会话时 `chat_id` 传 `undefined`，由后端返回 `session_init` 事件确定真实会话 ID。

### SSE 事件

`FetchEventSourceClient` 使用 `@microsoft/fetch-event-source` 连接 `POST /api/chat`。所有事件的 `data` 必须是 JSON；未知事件、空 event/data、非法 JSON 会被忽略。

当前前端支持的事件：

| event | data | 前端行为 |
|-------|------|----------|
| `session_init` | `{ "chat_id": string }` | 新会话采用服务端 ID |
| `reasoning` | `{ "delta": string }` | 追加到当前 reasoning buffer |
| `thinking_done` | `{}` | 标记 reasoning 完成，进入 `responding` |
| `assistant` | `{ "delta": string }` | 追加 assistant buffer |
| `assistant_done` | `{}` | 把 assistant buffer flush 为一条 assistant 消息 |
| `tool_call` | `{ "call_id": string, "tool_name": string, "params": object, "is_read_only": boolean, "server"?: string }` | 创建 `RUNNING` 工具调用 |
| `tool_result` | `{ "call_id": string, "execution_status": "SUCCEEDED" \| "FAILED", "output"?: object, "error"?: object, "execution_time_ms"?: number }` | 更新对应工具调用 |
| `tool_approval_required` | `{ "chat_id": string, "request_id": string, "tool_name": string, "params": object, "reason": string, "call_id": string }` | 显示内联审批，并把对应工具设为 `PENDING_APPROVAL` |
| `error` | `{ "code": string, "message": string }` | 停止流，显示连接/业务错误 |
| `done` | `{}` | 本轮结束，停止 streaming |

`tool_result` 如果找不到对应 `call_id`，前端只输出 console warning，不创建孤立工具条目。

## UI 行为

- `App.vue` 创建并 `provide` 全局依赖：SSE client、session API、approval API、stores、services、active workspace。
- `SessionList.vue` 按 Today、Yesterday、Previous 7 days、Previous 30 days、Older 分组展示会话；右键会话可删除。
- `ChatView.vue` 负责消息区、输入区、停止生成、错误横幅、滚动到底部按钮和审批事件分发。
- `MessageItem.vue` 渲染用户、assistant、system 消息；assistant 内容走 Markdown 渲染。
- `ToolCallInline.vue` 内联展示工具名、读写标记、前两个参数、执行状态和耗时。
- `ApprovalInline.vue` 内联展示审批原因、参数、可选备注、批准/拒绝按钮。
- `ReasoningBubble.vue` 展示 reasoning 过程。
- `ToastContainer.vue` 展示全局 toast。新会话首轮发送失败会提示“发送失败，请刷新页面重试”。

Markdown 渲染使用 `marked` + `DOMPurify`。链接只保留 `http:`、`https:`、`/`、`mailto:`、`#` 开头的 href。代码高亮通过 `highlight.js` 懒加载，目前注册 `bash`、`json`、`python`。

## 测试文档

### 单元测试

单元测试位于 `frontend/vue-project/src/__tests__/`，由 Vitest + jsdom 运行。

覆盖范围：

- store 行为：`chat-store`、`session-list-store`、`toast-store`
- 应用服务：`session-service`、`active-session-workspace`
- SSE 解释：`chat-stream-interpreter`
- transport/API：`sse-client`、`session-api`、`approval-api`
- 视图投影与 composables：`timeline-projection`、`use-chat`、`use-timeline`、`use-auto-scroll`
- Markdown 安全渲染
- 关键组件渲染

运行：

```bash
bun run test
bun run type-check
```

`vitest.config.ts` 使用 jsdom 环境，并排除 `tests/e2e/*.spec.ts` 与 `tests/e2e-live/*.spec.ts`。

### Mock E2E

mock 后端 E2E 位于 `frontend/vue-project/tests/e2e/`，配置文件为 `playwright.e2e.config.ts`。

- 默认 `baseURL` 为 `http://localhost:5173`
- 未设置 `E2E_SKIP_WEB_SERVER` 时，Playwright 会启动 `bun run dev --port 5174`
- 通过路由拦截 mock `/api/chat`、`/api/sessions`、`/api/tool-requests/*/approval`

运行：

```bash
bun run test:e2e
```

### Live E2E

真实后端 E2E 位于 `frontend/vue-project/tests/e2e-live/`，配置文件为 `playwright.e2e-live.config.ts`。

- 默认 `baseURL` 为 `http://localhost:5173`
- 不自动启动前端或后端服务，需要外部先启动完整环境
- 设置 `E2E_LIVE_HEADED=1` 可使用有头浏览器

运行：

```bash
bun run test:e2e:live
```

## 构建与部署

```bash
cd frontend/vue-project
bun run build
```

构建流程先执行 `vue-tsc --build`，再执行 Vite build。产物输出到 `frontend/vue-project/dist/`。

`frontend/nginx.conf` 负责托管静态文件，并把 `/api/` 代理给后端服务。前端代码中不写死后端 host。

## 相关图

前端模块依赖、聊天流、会话流和审批流见 `frontend/graph.md`。
