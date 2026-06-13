# frontend 测试

## 测试对象

| 模块 | 单元测试 | E2E |
|------|---------|-----|
| ChatStore | SSE 事件 → state 映射、审批状态机 | — |
| SessionListStore | 会话列表 CRUD 反映射 | — |
| SessionService | session CRUD + 审批编排 | — |
| ToastStore | 显示、dismiss、自动过期 | — |
| Markdown 渲染 | marked.js 输出 + 代码高亮 | — |
| 消息格式化 | 时间戳、类型标签、isMeta 渲染、timeline 排序 | — |
| 组件渲染 | SessionList、MessageItem、ToolCallInline、ApprovalInline | — |
| 完整用户路径 | — | 聊天→诊断→审批→执行 |

## 测试用例

### FE-001 SSE assistant 事件解析

| 输入 | `event: assistant\ndata: {"chat_id":"...", "delta":"当前 CPU"}` |
| 预期 | 解析为 AssistantEvent；delta 追加到当前消息 buffer；agentPhase='responding' |

### FE-002 SSE tool_call 事件解析

| 输入 | `event: tool_call\ndata: {"tool_name":"get_cpu_info","is_read_only":true}` |
| 预期 | toolCalls Map 插入条目；状态显示 RUNNING；timeline 中渲染 ToolCallInline |

### FE-003 SSE tool_result 事件解析

| 输入 | `event: tool_result\ndata: {"message_id":"...","execution_status":"SUCCEEDED","output":{...}}` |
| 预期 | 按 message_id 更新 Map 条目；ToolCallInline 显示状态和耗时 |

### FE-004 SSE tool_approval_required 事件

| 输入 | `event: tool_approval_required\ndata: {"request_id":"...","tool_name":"delete_temp_files","params":{...}}` |
| 预期 | session.approvalEvent 设置为 pending；timeline 渲染 ApprovalInline 卡片；显示 tool_name + params + 批准/拒绝按钮 |

### FE-005 SSE error 事件

| 输入 | `event: error\ndata: {"code":"LLM_CALL_FAILED","message":"..."}` |
| 预期 | connectionError 设置；ChatView 渲染 .connection-error 横幅；isStreaming=false |

### FE-006 SSE done 事件

| 输入 | `event: done\ndata: {"chat_id":"..."}` |
| 预期 | isStreaming=false；新 session 时 activeChatId 更新 + sessions 列表更新 |

### FE-007 Markdown 渲染

| 输入 | `"**粗体**\n- 列表\n\`\`\`bash\nls\n\`\`\`"` |
| 预期 | `<strong>` + `<ul>` + `<pre><code>` HTML 输出 |

### FE-008 审批批准回调

| 输入 | 用户点击 ApprovalInline 中的"批准" |
| 预期 | POST /api/tool-requests/{id}/approval；approval_status=APPROVED；卡片状态变为 approved |

### FE-009 审批拒绝回调

| 输入 | 用户点击"拒绝"，填写 message |
| 预期 | POST /api/tool-requests/{id}/approval；approval_status=REJECTED；message 保留在卡片中 |

### FE-010 会话切换

| 输入 | 点击侧边栏另一会话 |
| 预期 | GET /api/sessions/{chat_id}；shallowRef 替换 active ChatStore；timeline 刷新 |

### FE-011 Session 隔离

| 输入 | 在 session A 输入草稿 → 切换 B → 切回 A |
| 预期 | session B 重新从 API 加载历史；独立 ChatStore |

### FE-012 isMeta 消息渲染

| 输入 | Message 的 `isMeta=true`（如 "操作完成"） |
| 预期 | 居中灰色小字渲染，无气泡样式；isMeta 优先于 type 判断 |

### FE-013 Timeline 合并渲染

| 输入 | 消息序列：user → reasoning → assistant → tool_call → tool_result → assistant |
| 预期 | timeline computed 按 timestamp 合并排序；ToolCallInline 出现在对应位置；reasoning 可折叠 |

### FE-014 Toast 通知

| 输入 | SSE error 事件 |
| 预期 | 右上角 toast 弹出；不追加到 messages[]；自动 dismiss；只显示当前活跃 session 的 toast |

### FE-015 审批错误处理

| 输入 | POST /api/tool-requests/{id}/approval 返回 500 或网络错误 |
| 预期 | connectionError 设置；approvalEvent 保持 pending；用户可重试 |

### FE-016 Stream 中止

| 输入 | 用户点击 "Stop" 按钮（或切换 session） |
| 预期 | AbortController 中止 fetch；isStreaming=false；phase=idle；输入框重新启用 |

### FE-017 加载状态

| 输入 | 会话列表加载中 / 历史消息加载中 |
| 预期 | 列表区 spinner；ChatView loading overlay；操作完成后恢复正常 |

### FE-019 代码语法高亮

| 输入 | assistant 返回包含 ```bash / ```json / ```python 代码块的 Markdown |
| 预期 | `<pre><code>` 标签经过 highlight.js 处理，显示语法着色 |

### FE-020 Session 删除

| 输入 | 调用 deleteSession(chatId)（streaming 中） |
| 预期 | 列表项删除；若为 active session 则切回空白 ChatStore |
