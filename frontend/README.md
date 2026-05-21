# frontend

对话式运维聊天界面。本地开发构建，Docker 仅包含构建产物。

## 技术栈

| 依赖 | 用途 |
|------|------|
| Vue 3 | 组件化 UI 框架 |
| Bootstrap 5 | UI 样式与响应式布局 |
| marked.js | Markdown → HTML 渲染 |
| DOMPurify | HTML 清洗，防 XSS |
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
      main.ts           # 应用入口（createApp + Bootstrap + highlight.js）
      types.ts          # TypeScript 类型定义
      App.vue           # 根组件（布局 + 状态管理）
      assets/
        style.css       # Bootstrap 5 主题 + 自定义样式
      composables/
        useSSE.ts       # SSE 连接管理（fetch + ReadableStream）
        useChat.ts      # 聊天状态管理（消息 + 工具调用 + 审批）
        useSessions.ts  # 会话列表管理（创建 + 切换）
        useToast.ts     # Toast 通知队列
      components/
        ChatView.vue       # 聊天界面（消息列表、timeline、输入框）
        MessageItem.vue    # 单条消息渲染（用户/assistant/system/meta + Markdown）
        ToolCallCard.vue   # 工具执行卡片（状态 + 可折叠输出）
        ApprovalModal.vue  # 审批弹窗（批准/拒绝/提交状态）
        SessionList.vue    # 会话侧边栏（列表 + 加载状态）
        ToastContainer.vue # Toast 通知容器（右上角叠加）
    dist/               # Vite 构建产物（Docker 使用）
```

## 前端开发闭环

通过 Playwright MCP（`.claude/mcp.json`），支持agent自主完成：启动 dev server → 加载页面 → 截图验证 → 捕获运行时错误 → 修复 → 循环。

### 关键 Playwright MCP 工具

| 工具 | 用途 |
|------|------|
| `browser_navigate` | 加载页面（`http://localhost:5173`） |
| `browser_screenshot` | 全页截图，Claude 直接看到渲染结果 |
| `browser_console_messages` | 抓取 console.error / page error |
| `browser_network_requests` | 检查 API 请求是否成功 |
| `browser_snapshot` | 无障碍树快照，验证元素存在/文本内容 |
| `browser_click` / `browser_type` | 交互测试 |

### 典型流程

1. 后台启动 dev server：`cd frontend/vue-project && bun run dev &`
2. `curl -s -o /dev/null http://localhost:5173` 确认就绪
3. `browser_navigate` → `browser_screenshot` → `browser_console_messages`
4. 根据截图和错误日志修复代码
5. 循环 3-4 直到无错误
6. `bun run test:e2e` 最终验证
