# frontend 测试

## 测试对象

| 模块 | 单元测试 | E2E |
|------|---------|-----|
| SSE 事件解析 | 6 种事件类型解析 + 断线缓冲 | — |
| Markdown 渲染 | marked.js 输出 + 代码高亮 | — |
| 审批状态机逻辑 | 模态框显隐、按钮状态、提交错误重试、事件排队 | — |
| 消息格式化 | 时间戳、类型标签、isMeta 渲染、timeline 排序 | — |
| Toast 通知 | 显示、dismiss、自动过期、成功/错误/警告/信息 | — |
| 流控制 | Stream 中止、isStreaming 状态 | — |
| 加载状态 | 会话列表/创建/历史加载的 spinner | — |
| 可访问性 | ARIA 属性、focus trap、keyboard 导航 | — |
| 完整用户路径 | — | 聊天→诊断→审批→执行 |

## 测试用例

### FE-001 SSE assistant 事件解析

| 输入 | `event: assistant\ndata: {"chat_id":"...", "delta":"当前 CPU"}` |
| 预期 | 解析为 AssistantEventData；delta 追加到 buffer |

### FE-002 SSE tool_call 事件解析

| 输入 | `event: tool_call\ndata: {"tool_name":"get_cpu_info","isReadOnly":true}` |
| 预期 | 插入工具卡片；状态显示"执行中" |

### FE-003 SSE tool_result 事件解析

| 输入 | `event: tool_result\ndata: {"message_id":"...","execution_status":"SUCCEEDED","output":{...}}` |
| 预期 | 按 message_id 找到卡片；状态更新为"完成"；output 可折叠 |

### FE-004 SSE tool_approval_required 事件

| 输入 | `event: tool_approval_required\ndata: {"request_id":"...","tool_name":"delete_temp_files","params":{...}}` |
| 预期 | 弹出审批弹窗；显示 tool_name + params；启用批准/拒绝按钮 |

### FE-005 SSE error 事件

| 输入 | `event: error\ndata: {"code":"LLM_CALL_FAILED","message":"..."}` |
| 预期 | toast 提示错误；输入框仍可用 |

### FE-006 SSE done 事件

| 输入 | `event: done\ndata: {"chat_id":"..."}` |
| 预期 | 关闭 EventSource；启用输入框 |

### FE-007 Markdown 渲染

| 输入 | `"**粗体**\n- 列表\n```bash\nls\n```"` |
| 预期 | `<strong>` + `<ul>` + `<pre><code>` HTML 输出 |

### FE-008 审批批准回调

| 输入 | 用户点击"批准" |
| 预期 | POST /api/tool-requests/{id}/approval；approval_status=APPROVED |

### FE-009 审批拒绝回调

| 输入 | 用户点击"拒绝"，填写 reason |
| 预期 | POST /api/tool-requests/{id}/approval；approval_status=REJECTED + reason |

### FE-010 会话切换

| 输入 | 点击侧边栏另一会话 |
| 预期 | GET /api/sessions/{chat_id}；消息列表刷新 |

### FE-011 SSE 断线重连

| 输入 | EventSource 连接断开（服务端关闭或网络中断） |
| 预期 | onerror 触发；前端自动重连（EventSource 默认行为）；重连后新消息正常接收；buffer 中未完整消息丢弃，不显示残片 |

### FE-012 isMeta 消息渲染

| 输入 | Message 的 `isMeta=true`（如 "审批已通过"、"工具执行被拒绝"） |
| 预期 | 居中灰色小字渲染，无气泡样式，不参与对话排版；isMeta 优先于 type 判断 |

### FE-013 Timeline 合并渲染

| 输入 | 消息序列：user → assistant(delta) → tool_call → tool_result → assistant(delta) |
| 预期 | timeline computed 按 timestamp 合并排序；ToolCallCard 出现在两段 assistant 文本之间，而非所有消息末尾；tool_call/tool_result 不出现在 messages[] 中 |

### FE-014 Toast 通知

| 输入 | SSE error 事件（code + message） |
| 预期 | 右上角 toast 弹出，显示错误码和消息；不追加到 messages[]（非持久）；自动 dismiss 或手动关闭；致命错误仍写入 system message |

### FE-015 审批提交错误重试

| 输入 | POST /api/tool-requests/{id}/approval 返回 500 或网络错误 |
| 预期 | 弹窗不关闭；toast 显示错误信息；用户可再次点击批准/拒绝重试 |

### FE-016 审批期间事件排队

| 输入 | tool_approval_required 触发后（modal 显示中），SSE buffer 中收到后续事件 |
| 预期 | 事件入队列暂存；审批提交成功后 drain 队列按序处理；拒绝后丢弃队列 |

### FE-017 Stream 中止

| 输入 | 用户点击 "Stop" 按钮 |
| 预期 | AbortController 中止 fetch；isStreaming=false；输入框重新启用；abort emit 到 App.vue |

### FE-018 加载状态

| 输入 | 创建会话 API 请求中 / 会话列表加载中 / 历史消息加载中 |
| 预期 | 对应按钮禁用 + spinner；列表区骨架屏或 spinner overlay；操作完成后恢复正常状态 |

### FE-019 代码语法高亮

| 输入 | assistant 返回包含 ```bash / ```json / ```python 代码块的 Markdown |
| 预期 | `<pre><code>` 标签经过 highlight.js 处理，显示语法着色 |

### FE-020 可访问性

| 输入 | 键盘 Tab / Enter / Escape 操作 |
| 预期 | ApprovalModal focus trap（Tab 在批准/拒绝/备注间循环）；SessionList 支持 Enter/Space 选择会话；ChatView 消息区 aria-live 通知屏幕阅读器 |
