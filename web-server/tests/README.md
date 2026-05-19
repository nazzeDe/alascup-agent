# web-server 测试

## 测试对象

| 模块 | 单元测试 | 集成测试 |
|------|---------|---------|
| 数据模型（Pydantic） | 字段校验、序列化 | — |
| 规则引擎 | rules.json 匹配 + classify_tool 分级判定 | — |
| 审查层集成 | — | classify_tool → 规则匹配 → 审批 完整链路 |
| ToolRequest 状态机 | PENDING→APPROVED/REJECTED/EXPIRED | — |
| 审计日志 | 事件格式化 | 写入验证 |
| Agent ReAct 循环 | 状态流转 | 完整 Agent 链路 |
| OpenAPI 路由 | 请求/响应格式 | SSE 流式事件 |
| MCP Client | 连接管理 mock | tool-server 通信（含 classify_tool）|
| 可观测性 | 调试日志门控、剖析器开关、错误日志缓冲 | — |
| LLM 错误恢复 | prompt_too_long / max_output_tokens / model_fallback 恢复路径 | — |

## 测试用例

### SC-001 只读工具自动审批

| 前置 | get_cpu_info is_read_only=true，rules.json 未命中 |
| 输入 | LLM 生成 get_cpu_info tool_call |
| 预期 | 自动审批通过；审计事件 AUTO_APPROVED level=INFO |

### SC-002 黑名单拒绝

| 前置 | get_cpu_info 在 rules.json blacklist |
| 输入 | LLM 生成 get_cpu_info tool_call |
| 预期 | 拒绝执行；审计 level=WARN；不向 tool-server 发起调用 |

### SC-003 白名单覆盖

| 前置 | delete_temp_files（is_read_only=false）在 whitelist |
| 输入 | LLM 生成 delete_temp_files tool_call |
| 预期 | 自动审批通过；审计 AUTO_APPROVED level=WARN |

### SC-004 高风险生成 ToolRequest

| 前置 | restart_service is_read_only=false，未在 rules.json |
| 输入 | LLM 生成 restart_service tool_call |
| 预期 | 生成 request_id，PENDING；审计 TOOL_REQUEST_CREATED |

### SC-005 审批通过

| 前置 | ToolRequest PENDING |
| 输入 | POST approval_status=APPROVED |
| 预期 | 状态 APPROVED；审计 APPROVED level=WARN；调用 tool-server 携带 approval_status=APPROVED |

### SC-006 审批拒绝

| 前置 | ToolRequest PENDING |
| 输入 | POST approval_status=REJECTED，reason="不需要" |
| 预期 | 状态 REJECTED；审计 REJECTED；LLM 收到拒绝反馈含原因 |

### SC-007 审批超时

| 前置 | ToolRequest PENDING |
| 输入 | 等待超时（测试设 3s） |
| 预期 | 自动 EXPIRED；审计 EXPIRED；LLM 被告知超时 |

### SC-008 二次校验拦截未审批请求

| 前置 | tool-server 正常 |
| 输入 | web-server 发 approval_status=PENDING 的 tool_call |
| 预期 | tool-server 拒绝，返回 SECURITY_VIOLATION；web-server 记录 CRITICAL |

### AG-001 ReAct 基本循环

| 前置 | LLM mock，tool-server mock |
| 输入 | 用户消息 "查看系统 CPU" |
| 预期 | Thought → tool_call → tool_result → assistant message 完整流转 |

### AG-003 并发 tool_call（全只读）

| 前置 | LLM mock 单次返回两个只读 tool_call |
| 输入 | 用户消息 "查看 CPU 和内存" |
| 预期 | 并行执行；两个 tool_result 按序推送 |

### AG-004 并发 tool_call（只读+高风险）

| 前置 | LLM mock 返回一个只读 + 一个高风险 |
| 输入 | 用户消息 "检查 CPU 并重启服务" |
| 预期 | 只读立即执行推送；高风险串行等审批 |

### AG-005 上下文压缩（两层）

| 前置 | context_window_size=1000（测试缩小），长对话积累多条消息，token 超安全阈值（默认 70% 窗口）|
| 输入 | 每轮 LLM 调用前检查 token 用量 |
| 预期 | 第 1 层：截断超长 tool_result 的详细输出，保留摘要字段。仍超阈值则第 2 层：调轻量 LLM 对早期消息生成摘要，近期消息和工具结果优先保留原始内容。压缩后 token 数降至阈值以下 |

### EH-001 连接超时重试

| 前置 | tool-server 不可达 |
| 预期 | 重试 2 次（间隔 1s/2s）；仍失败反馈 LLM "tool unavailable" |

### EH-002 调用超时不重试

| 前置 | tool-server 已连接但 20s 无响应 |
| 预期 | 标记 FAILED；不重试；LLM 收到超时反馈 |

### EH-003 prompt_too_long 恢复

| 前置 | LLM mock 返回 prompt_too_long 错误 |
| 输入 | 上下文压缩可用 |
| 预期 | 第 1 层：截断+摘要压缩后重试。仍失败则第 2 层：激进压缩后重试。仍失败则表面错误给用户 |

### EH-004 max_output_tokens 恢复

| 前置 | LLM mock 返回 max_output_tokens 错误 |
| 预期 | 第 1 层：提升 token 上限透明重试。仍超则第 2 层：注入 "continue" meta message，最多 3 次。超过 3 次则表面错误 |

### EH-005 model_fallback 恢复

| 前置 | LLM mock 返回 503/529，fallback_model 已配置 |
| 预期 | 切换到 fallback model，重试一次。成功则继续，失败则表面错误 |

### AG-006 transition 追踪

| 前置 | 完整高风险操作流程 |
| 输入 | user_message → tool_results → approval_pending → approval_granted → done |
| 预期 | 每个 transition 写入 audit_events（event=LOOP_TRANSITION），按 chat_id 可追溯完整轨迹 |

### AG-007 流式工具执行

| 前置 | LLM 流式输出中返回两个只读 tool_use block |
| 预期 | 第一个 tool_use 解析完成后立即入队执行（与 LLM 后续输出并行），第二个同理；流结束后工具结果均已就绪 |

### CL-001 classify_tool 集成

| 前置 | tool-server mock 就绪 |
| 输入 | web-server 审查层收到 tool_call，调用 classify_tool 查询分级 |
| 预期 | tool-server 返回 is_read_only/is_rollbackable；web-server 使用分级结果做规则匹配 |

### OB-001 调试日志门控

| 前置 | ALASCUP_DEBUG 未设置 |
| 预期 | debug()/info()/warn()/error() 调用无文件输出，仅写入内存环形缓冲 |

### OB-002 剖析器门控

| 前置 | ALASCUP_PROFILE 未设置 / 设置为 1 |
| 预期 | 未设置：checkpoint() 零开销返回，无报告。设置为 1：有检查点记录，每轮结束产出时间线报告 |

### AL-001 全链路审计

| 前置 | 完整高风险操作流程 |
| 预期 | 审计事件链完整：AUTO_APPROVED → TOOL_REQUEST_CREATED → APPROVED → TOOL_EXECUTED；chat_id 一致 |

### API-001 SSE 流式响应

| 输入 | POST /api/chat-turn |
| 预期 | Content-Type: text/event-stream；事件格式符合 SSE 规范 |

### API-002 会话生命周期

| 输入 | POST 创建 → GET 列表 → GET 详情 → POST 追加 |
| 预期 | 创建 201；列表含新会话；详情含 messages |

## Mock 配置

### LLM Mock 响应序列

**只读诊断**：

```
turn 1: user="查看 CPU" → ToolCall: search_experience
turn 2: tool_result(results) → ToolCall: get_cpu_info
turn 3: tool_result(cpu_percent:85) → Assistant: "当前 CPU 占用 85%..."
```

**高风险含审批**：

```
turn 1: user="清理磁盘" → ToolCall: get_disk_usage
turn 2: tool_result(disk_usage:92%) → Assistant: "建议清理 /tmp/logs。确认？"
turn 3: user="确认" → ToolCall: delete_temp_files
turn 4: tool_result(deleted:2.3GB) → Assistant: "已释放 2.3GB"
```

**审批拒绝**：

```
turn N: tool_result(approval_status:REJECTED, reason:"不需要")
       → Assistant: "delete_temp_files 被您拒绝。请给出新指示。"
```

**执行失败**：

```
turn N: tool_call: restart_service → tool_result(FAILED, "permission denied")
       → Assistant: "重启失败（权限不足），请给出新指示。"
```

### 测试 rules.json

```json
{
  "whitelist": [{ "tool_name": "delete_temp_files" }],
  "blacklist": [{ "tool_name": "reboot_system" }]
}
```

| 工具 | 分级 |
|------|------|
| get_cpu_info | is_read_only=true → 只读 |
| get_disk_usage | is_read_only=true → 只读 |
| delete_temp_files | is_read_only=false, is_rollbackable=true → 可回滚 |
| restart_service | is_read_only=false, is_rollbackable=false → 破坏性 |
| reboot_system | is_read_only=false → 黑名单 |

### 时间加速

审批超时测试通过依赖注入缩短：

```python
# 生产 300s，测试 3s
TOOL_REQUEST_TIMEOUT_SECONDS = 3
```
