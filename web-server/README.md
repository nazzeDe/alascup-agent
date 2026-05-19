# web-server

核心编排层。接收用户消息，驱动 Agent 循环（ReAct 模式），作为 MCP Client 调用 tool-server 和 rag-server。

## 职责

- 管理聊天会话生命周期
- 驱动 Agent 循环（ReAct：Thought → Action → Observation）
- 作为 MCP Client 调用 tool-server（感知 + 操作）和 rag-server（经验检索）
- 执行审查层：规则匹配 + ToolRequest 审批流转
- 记录全链路审计日志到 PostgreSQL
- 自定义 Tracer 记录 LLM 调用追踪（token 消耗、延迟、响应内容）
- 通过 SSE 流式推送 Agent 状态到前端

## 非目标

- 不直接访问宿主机文件系统
- 不执行实际运维命令
- 不存储向量数据
- 不做知识库文本预处理
- 不对工具参数做安全分级判断——分级由 tool-server 的 classify_tool 负责

## 技术栈

| 依赖 | 用途 |
|------|------|
| FastAPI | HTTP 框架 + OpenAPI 自动生成 |
| LangGraph | Agent 状态机（StateGraph, astream, interrupt） |
| fastmcp | MCP Client，连接 tool-server 和 rag-server |
| Pydantic | 数据校验（FastAPI 内置） |
| httpx | LLM API 调用（OpenAI 兼容） |
| asyncpg | PostgreSQL 异步驱动（会话、审计、LLM 追踪） |
| uvicorn | ASGI 服务器 |
| sse-starlette | SSE 流式响应 |

## 数据存储

web-server 使用 PostgreSQL 作为唯一持久化存储：

| 表 | 用途 |
|------|------|
| `chat_sessions` | 会话元数据 |
| `messages` | 消息历史 |
| `tool_calls` | 工具执行记录 |
| `tool_requests` | 审批请求状态 |
| `audit_events` | 审计日志（仅 INSERT/SELECT，不可变） |
| `llm_traces` | LLM 调用追踪（token、延迟、响应） |

完整 Schema 见 `doc/数据库设计.md`。

## 依赖注入

每个模块通过接口（Protocol）定义契约，生产与测试环境注入不同实现。关键可替换组件：

| 接口 | 生产实现 | 测试实现 |
|------|----------|----------|
| SessionManager | PostgresSessionManager | InMemorySessionManager |
| AuditLogger | PostgresAuditLogger | InMemoryAuditLogger |
| Tracer | PostgresTracer | NullTracer |
| LLMAdapter | 真实 LLM API 调用 | MockLLMAdapter |
| RuleEngine | 读取 config/rules.json | 独立 rules 路径 |

无 DATABASE_URL 时自动降级为内存实现，方便本地开发。

## Agent 循环

流程图详见 `graph.md`。

Agent 循环以 ReAct 模式（Thought → Action → Observation）运行。每个 chat-turn 中：

1. 从 Session 加载历史消息，组装 system prompt（按 section 合并，结构见 `doc/详细设计.md`），进入循环
2. 每轮迭代中 LLM 流式输出，产出文本或 tool_call
3. 所有 tool_call 进入审查层：只读放行，高风险生成审批请求
4. 审批通过后调用 tool-server/rag-server 执行，结果回写消息历史
5. LLM 判断任务完成或无 tool_call 时退出循环
6. 循环过程中通过 SSE 流式推送状态到前端

关键行为：
- `astream()` 在每个节点完成后产出状态，web-server 映射为 SSE 事件
- 高风险 tool_call 通过 `interrupt()` 暂停图执行，等待审批回调后恢复
- 每轮 LLM 调用前主动检查 token 用量，超阈值时分层压缩
- LLM 返回非终端错误（prompt_too_long、max_output_tokens、model overload）时逐层升级恢复

### Prompt Section 默认值

每个 section 的内置默认内容。可通过 `prompt.md` 按 section 名覆盖。

**identity**
> 你是 AI 运维 Agent，负责诊断系统问题、分析性能指标、执行审批通过的修复操作。先收集信息，再给出判断。

**rules**
> 你必须遵守以下规则：
> - 未经用户明确审批，不得执行破坏性操作
> - 每次工具调用前验证其必要性
> - 所有决策和操作记录审计日志

**tool_usage**
> 工具调用规范：
> - 先收集信息再行动——优先使用只读工具了解系统状态
> - 并行调用独立的只读工具以加速信息收集
> - 验证每个工具的执行结果，失败时分析原因并调整策略
> - 高风险操作先输出计划再请求执行

**environment**

由 tool-server 启动时上报，PromptManager 构建。包含：

| 字段 | 说明 |
|------|------|
| `target_host` | 目标主机 hostname |
| `target_os` | 操作系统及版本（如 `Linux 7.0 (loongarch64)`） |
| `mount_scope` | Agent 可访问的系统资源（只读挂载点：`/proc`, `/sys`, `/var/log`） |
| `system_time` | 目标系统当前时间（ISO 格式） |

**memory**
> （由 PromptManager 从记忆系统加载，无持久记忆时为空字符串。格式见 `doc/详细设计.md` 的 System Prompt 结构）

### Transition 追踪

Agent State 中持久化 `transition` 字段，记录状态变更原因。每个节点返回时附带当前 transition，供调试和审计。

| transition | 触发条件 |
|------------|----------|
| `user_message` | 用户新消息进入 |
| `tool_results` | 工具执行完成，继续推理 |
| `approval_pending` | 高风险工具暂停等审批 |
| `approval_granted` | 审批通过，恢复执行 |
| `approval_rejected` | 审批拒绝 |
| `context_compacted` | 上下文压缩后重试 |
| `max_output_tokens_recovery` | token 上限恢复重试 |
| `model_fallback` | 模型降级重试 |
| `done` | LLM 判断结束 |
| `error_exit` | 异常退出 |

### 退出条件

| 条件 | 行为 |
|------|------|
| LLM 无 tool_call 且 stop_reason=end | transition=`done`，退出 |
| abort / 连续失败 | transition=`error_exit`，退出 |

### 流式工具执行

LLM 流式输出过程中，think 节点检测到完整的 tool_use block 后，只读工具立即发起执行，与 LLM 后续 token 生成并行。高风险工具不适用——必须等完整响应后走安全分类和审批。

```
LLM 流式输出 token
  │
  ├─ chunk: "Let me check CPU first..."      ← 文本，暂存
  ├─ chunk: {"tool_use": "get_cpu_info"}     ← 解析到 tool_use → 立即入队
  │   tool-server.get_cpu_info() 开始执行 ─────────────→ 与 LLM 输出并行
  ├─ chunk: "Also memory..."                 ← LLM 继续输出
  ├─ chunk: {"tool_use": "get_memory_info"}  ← 再入队
  │   tool-server.get_memory_info() 开始执行 ──────────→ 并行
  └─ LLM 输出完成
      → 此时工具结果可能已全部就绪
```

实现依赖 LangGraph 的 `astream_events()`（token 级别），在 think 节点内边消费 token 边分发已完成的 tool_use block 到 act 节点。

### 上下文压缩

每轮 LLM 调用前主动检查 token 用量，超阈值时分层压缩：

```
估算 token 用量
  │
  ├─ 未超安全阈值 → 直接送入 LLM
  │
  └─ 超阈值 →
       1. Tool result 截断（截掉超长 tool_result 的详细输出，保留摘要字段）
          → 重算 → 仍超？
       2. 调轻量 LLM 对早期消息生成摘要（近期消息和工具结果优先保留原始内容）
          → 仍超？
       3. 返回错误，无法继续
```

`安全阈值` 可配置，默认为模型上下文窗口的 70%。`轻量 LLM` 可配置为成本较低的模型。

**Compact Prompt 设计**

第 2 层 LLM 摘要时使用专用 compact prompt：
- **角色**：要求模型做摘要，不是继续对话
- **禁止工具调用**：preface 明确 "Text only, no tools"，防止模型在摘要时尝试调工具
- **输出格式**：结构化摘要，用标签包裹便于解析

```
系统: "Text only, no tools. 总结以下对话。提取：关键请求、技术发现、已完成操作、待处理事项。用 <summary> 标签包裹。"

用户:  [早期消息历史]
      ---
      请生成摘要。
```

原始对话中近期消息和工具结果保留不压缩，摘要替换早期消息。

### 可观测性

#### 检查点剖析器

环境变量 `ALASCUP_PROFILE=1` 启用。在 agent 循环各关键步骤插入检查点，记录耗时和内存。每轮结束时产出时间线报告，自动标记 >100ms 的慢操作。

报告写入 debug 日志，不通过 SSE 推送到前端。

#### 错误日志

所有错误统一通过错误日志模块记录：

- **内存环形缓冲**：保留最近 N 条错误，支持运行时查询和 bug report
- **持久化文件**：每次错误追加写入文件，按日期滚动
- **MCP 错误独立存储**：tool-server / rag-server 的通信错误单独记录，方便排查连接问题

错误日志在依赖注入就绪之前即可安全调用（消息先入队列，sink 就绪后 drain）。

#### 成本追踪

每次 LLM 调用后累计 token 用量和费用，按模型聚合。数据来源为 LLM API 响应中的 `usage` 字段。累计数据跨会话持久化，重启后恢复。支持查询本次会话的总 token 消耗和费用。

#### 调试日志

环境变量 `ALASCUP_DEBUG=1` 启用。分级输出 Agent 决策叙事——为什么进入这个分支、为什么触发压缩、恢复策略选择原因等。非结构化数据（LLM 调用详情、工具执行结果）由 Tracer 和审计日志负责，调试日志不记录。

- **级别**：DEBUG（高频细节）/ INFO（关键决策）/ WARN（可恢复异常）/ ERROR（不可恢复错误）。默认 INFO，`ALASCUP_DEBUG_LEVEL=DEBUG` 升到 DEBUG
- **过滤**：`ALASCUP_DEBUG_FILTER=agent,security` 只输出含关键词的行
- **输出**：`logs/debug/<timestamp>-<session>.log`，同时维护 `logs/debug/latest` symlink
- **写入策略**：显式开启时同步写入（防 crash 丢数据），未开启时仅内存环形缓冲（最近 500 条）

## SSE 事件契约

`POST /api/chat-turn` 返回 `text/event-stream`，事件定义：

| event | data | 说明 |
|-------|------|------|
| `assistant` | `{chat_id, message_id, delta}` | LLM 文本流式输出 |
| `tool_call` | `{chat_id, message_id, tool_name, params, isReadOnly}` | LLM 请求调用工具 |
| `tool_result` | `{chat_id, message_id, tool_name, execution_status, output?}` | 工具执行结果 |
| `tool_approval_required` | `{chat_id, request_id, tool_name, params, reason}` | 高风险工具需审批，流暂停 |
| `error` | `{code, message}` | 异常 |
| `done` | `{chat_id}` | 流结束 |

只读工具 `tool_call`/`tool_result` 流式推送。高风险触发 `tool_approval_required` 后 SSE 暂停，`POST /api/tool-requests/{request_id}/approval` 回调后流继续。

## 配置

运行时需 `config/` 目录包含：

- `servers.json` — MCP Server 连接配置
- `prompt.md` — System prompt 模板（可选，按 section 名覆盖默认值，未覆盖的 section 使用内置默认值）
- `rules.json` — 工具调用安全规则（可选）
- `llm.json` — LLM 接入配置

环境变量：

- `DATABASE_URL` — PostgreSQL 连接串（可选，不设时使用内存实现）

## API 端点

| 端点 | 说明 |
|------|------|
| GET /api/health | 健康检查 |
| POST /api/chat-turn | 聊天入口（SSE 流式） |
| GET /api/sessions | 列出全部会话 |
| POST /api/sessions | 创建新会话 |
| GET /api/sessions/{chat_id} | 获取会话详情 |
| GET /api/tools | 列出可用 MCP 工具 |
| GET /api/tool-requests/{request_id} | 查询审批请求状态 |
| POST /api/tool-requests/{request_id}/approval | 审批回调 |

完整规范见 `doc/api-spec/openapi.yaml`。
