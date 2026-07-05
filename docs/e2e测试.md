# E2E 测试

E2E 测试验证用户视角的完整链路。项目分为 Mock E2E 和 Live E2E 两类：前者用于快速回归前端交互，后者用于比赛前确认真实服务栈可用。

## 测试类型

| 类型 | 目录 | 后端依赖 | 适用场景 |
|------|------|----------|----------|
| Mock E2E | `frontend/vue-project/tests/e2e/` | 不需要真实后端 | 前端交互、SSE 解析、审批 UI、错误恢复 |
| Live E2E | `frontend/vue-project/tests/e2e-live/` | 需要 Docker 测试栈 | 部署连通性、真实 API、真实 SSE、会话持久化 |

## Mock E2E

执行：

```bash
cd frontend/vue-project
bun run test:e2e
```

Mock E2E 使用 Playwright route 拦截：

- `POST /api/chat`
- `GET /api/sessions`
- `GET /api/sessions/{chat_id}`
- `DELETE /api/sessions/{chat_id}`
- `POST /api/tool-requests/{request_id}/approval`，请求体包含 `chat_id` 与决策

它验证前端是否正确处理 `session_init`、`tool_call`、`tool_result`、`tool_approval_required`、`assistant`、`assistant_done`、`done` 和 `error` 等 SSE 事件。

## Live E2E

执行：

```bash
make up-test
curl http://localhost/api/health
cd frontend/vue-project
E2E_BASE_URL=http://localhost bun run test:e2e:live
```

或直接执行：

```bash
make test-e2e-live
```

结束后清理：

```bash
make down-test
```

Live E2E 连接真实 nginx、web-server、tool-server 和 PostgreSQL。它不验证复杂 LLM 语义，而是验证测试栈是否能真实启动、API 是否可用、SSE 是否能开始推送、会话是否能持久化。

## Mock E2E 用例

### E2E-001 只读诊断

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 打开页面 | 聊天输入框和空状态可见 |
| 2 | 发送 “查看当前系统 CPU 占用” | 用户消息立即出现在消息列表 |
| 3 | 接收工具事件 | 显示 `get_cpu_info` 工具调用，状态为 Done |
| 4 | 接收助手消息 | assistant message 含 CPU 和 85% |

### E2E-002 高风险操作审批通过

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 发送 “清理磁盘空间” | 显示 `get_disk_usage` 只读诊断工具 |
| 2 | 发送 “确认执行” | 显示 `delete_temp_files` 审批块 |
| 3 | 点击 Approve | 审批块状态为 Approved，执行工具状态为 Done |

### E2E-003 高风险操作审批拒绝

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 发送 “重启 nginx 服务” | 显示审批块和工具参数 |
| 2 | 填写拒绝原因并点击 Reject | 审批块状态为 Rejected，并显示拒绝原因 |
| 3 | 不填写原因直接 Reject | 审批块仍更新为 Rejected |

### E2E-004 审批超时

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 模拟高风险 tool approval | 页面显示审批块 |
| 2 | 模拟后端超时错误 | 页面显示错误横幅，提示 approval expired |

### E2E-005 SSE 错误恢复

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 模拟 `/api/chat` 返回错误 | 页面显示 Connection lost |
| 2 | 再次发送消息 | 输入框仍可用，后续 assistant 消息能显示 |

### E2E-006 会话管理

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 新会话发送消息 | 侧边栏新增会话 |
| 2 | 点击另一会话 | 会话项 active，消息区切换 |
| 3 | 点击 New Session | 回到空聊天状态 |

## Live E2E 用例

### E2E-Live-000 健康检查

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 请求 `/api/health` | 返回 `status=ok` |
| 2 | 请求 `/api/tools` | 返回非空工具列表 |

### E2E-Live-001 聊天流

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 打开页面 | 聊天输入框和 New Session 按钮可见 |
| 2 | 新建会话并发送消息 | 用户消息可见，assistant 消息最终出现 |
| 3 | 发送长请求 | 流式处理中输入框禁用，防止重复提交 |

### E2E-Live-002 会话持久化

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 请求 `/api/sessions` | 返回数组 |
| 2 | 前端发送消息创建会话 | 侧边栏出现该会话 |
| 3 | 刷新页面 | 会话列表仍包含持久化记录 |

## 设计原则

- Mock E2E 断言具体 UI 状态，快速定位前端回归。
- Live E2E 断言真实链路可用，避免依赖不稳定的自然语言回复内容。
- 选择器应反映当前组件结构，优先使用稳定 class 或可见文本。
- 超时测试只验证用户可见结果，不依赖内部定时器精确时间。
