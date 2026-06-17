# E2E 测试

端到端测试覆盖完整用户路径，使用 Playwright 驱动浏览器。当前分两类：

- `frontend/vue-project/tests/e2e/`：mock 后端的前端 E2E，默认由 `make test-e2e` 执行，不需要启动 web-server/tool-server。
- `frontend/vue-project/tests/e2e-live/`：连接真实后端的 live E2E，需要先启动完整测试环境。

## 前置

### Mock E2E

```bash
cd frontend/vue-project
bun run test:e2e
```

Playwright 通过 route mock `/api/chat`、`/api/sessions` 和 `/api/tool-requests/{request_id}/approval`。测试数据见 `docs/测试数据规格.md`。

### Live E2E

1. 启动测试环境：`make up-test`
2. 确认 `http://localhost/api/health` 返回健康状态
3. 执行：

```bash
cd frontend/vue-project
E2E_BASE_URL=http://localhost bun run test:e2e:live
```

## 测试用例

### E2E-001 只读诊断

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 打开页面 | 聊天界面加载完成，输入框可用 |
| 2 | 输入 "查看当前系统 CPU 占用" 并发送 | 消息列表新增用户消息 |
| 3 | 等待 SSE 流式响应 | 依次出现 `get_cpu_info` 内联工具调用、分析结论 |
| 4 | 检查最终 assistant message | 含 CPU 占用百分比和建议 |

### E2E-002 高风险操作审批通过

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 输入 "清理磁盘空间" 并发送 | Agent 先返回只读诊断 + 清理计划 |
| 2 | 输入 "确认执行" | 出现内联审批块，显示 `delete_temp_files` 工具参数 |
| 3 | 点击 "Approve" | 审批块状态更新为 Approved，工具调用更新为 Done，assistant 返回执行总结 |

### E2E-003 高风险操作审批拒绝

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 输入 "重启 nginx 服务" 并发送 | 出现内联审批块 |
| 2 | 填写拒绝原因 "暂不需要"，点击 "Reject" | 审批块状态更新为 Rejected，assistant 告知被拒原因，不执行重启 |

### E2E-004 审批超时

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 触发高风险 tool_call | 内联审批块显示 |
| 2 | 等待超时（测试通过 mock/依赖注入缩短等待） | 审批状态变为过期或 assistant 告知审批已过期 |

### E2E-005 SSE 错误恢复

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 正常对话进行中 | SSE 流正常 |
| 2 | 模拟 SSE/后端错误 | 页面显示错误横幅；新会话首轮失败时显示 toast |
| 3 | 输入新消息 | 输入框仍可用，可继续对话 |

### E2E-006 会话管理

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 新会话发送消息并收到 `session_init` | 侧边栏新增会话条目 |
| 2 | 点击侧边栏另一会话 | 消息列表切换为该会话内容 |
| 3 | 右键会话并删除 | 前端调用 `DELETE /api/sessions/{chat_id}`，侧边栏移除该会话 |

## 测试数据初始化

Mock E2E 的 SSE 序列和 REST 响应在 `frontend/vue-project/tests/e2e/helpers.ts` 中通过 Playwright route 构造。Live E2E 的服务侧测试数据见 `docs/测试数据规格.md`，各服务初始化脚本见对应 `*/tests/` 目录。
