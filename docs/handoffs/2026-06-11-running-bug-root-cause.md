# Bug 根因分析: 工具执行结束后前端显示 "Running…"

## 调查方法

- 服务端代码逐行追踪（orchestrator、approval、think、act、observe、transitions）
- 前端代码逐行追踪（useSessionManager、ToolCallInline、ChatView）
- SSE 事件流实时验证（curl + 前端代理）
- 服务端日志分析（`logs/services/web-server.log`）
- 已有 handoff 文档对照（`docs/handoffs/2026-06-11-bug-report-*`）

---

## 影响范围

| 场景 | 表现 | 频率 |
|------|------|------|
| **A**: 单工具 + 自动审批 | 工具显示 Done → 正常 | 正常 ✅ |
| **B**: 多工具 + 自动审批 | 工具显示 Done → 正常 | 正常 ✅ |
| **C**: 单工具 + 需要审批 | 审批 → 执行 → Done | 正常 ✅ |
| **D**: 多工具 + 混合审批(部分自动+部分需审批) | ❌ "Running…" 残留 | 必现 🔴 |
| **E**: 审批后 LLM 再生成新工具 | ❌ 新工具的 ToolCallStarted 被跳过 | 必现 🟡 |
| **F**: 连接异常中断 | ❌ RUNNING 工具永不清理 | 偶发 🟡 |

---

## Bug 1 (Primary): `clear_transient_fields` 清空 `pending_approval`

**文件**: `web-server/src/agent/loop/transitions.py:9-16`
**行**: `TRANSIENT_FIELDS = ("tool_calls", "approved_tool_calls", "rejected_tool_calls", "pending_approval", "tool_results", "streaming_tool_results")`

### 代码

```python
def clear_transient_fields(state: dict) -> None:
    for key in TRANSIENT_FIELDS:
        state[key] = []
```

### 问题

`clear_transient_fields` 在 `orchestrator.py:264` 被调用，即**内审批循环之后、外循环继续之前**。它清除了 `pending_approval`，但 `_handle_transition` 可能因为 `transition` 仍然是 `APPROVAL_PENDING` 而返回 `"continue"`，导致外循环继续但 `resolve()` 找不到待审批的工具。

### 调用链

```
内审批循环:
  resolve(pending_approval=[tool1]) → 批准 → APPROVAL_GRANTED
  emit ToolCallStarted(tool1)
  graph.ainvoke → 执行 tool1 → LLM → 生成 tool2(需审批)
  → transition=APPROVAL_PENDING
  emit ToolCallFinished(tool1)
  pending_approval=[tool2] → 不 break → 继续内循环

  resolve(tool2) → 批准 → APPROVAL_GRANTED
  emit ToolCallStarted(tool2)
  graph.ainvoke → 执行 tool2 → LLM → 文本(无新工具)
  → transition=DONE
  emit ToolCallFinished(tool2)
  pending_approval=[] → break

-- 以下场景会出问题 --

内循环 graph.ainvoke 返回多个待审批工具:
  → tool_calls=[tool3, tool4], pending_approval=[tool3, tool4]
  → transition=APPROVAL_PENDING
  → 内循环 line 183:
    if transition not in (APPROVAL_GRANTED, APPROVAL_REJECTED): break
    → BREAK! 因为 APPROVAL_PENDING 不在集合中

  _emit_sse → pending_ids 正确跳过 tool3, tool4 ✅
  clear_transient_fields → 清空 pending_approval ❌
  _handle_transition(APPROVAL_PENDING) → "continue"
  
  外循环继续:
    resolve(pending_approval=[]) → 啥也不做 → return
    graph.ainvoke → LLM 基于历史回复 → 可能正常 ❓
    → tool3, tool4 **永远丢失**
```

### 根本原因

内循环的退出条件（line 183）在处理完所有审批批次前就退出了。`APPROVAL_PENDING` 不在 `(APPROVAL_GRANTED, APPROVAL_REJECTED)` 检查范围内。

### 修复方向

方案 A: `clear_transient_fields` 跳过 `pending_approval`：

```python
def clear_transient_fields(state: dict) -> None:
    for key in TRANSIENT_FIELDS:
        if key != "pending_approval":
            state[key] = []
```

方案 B: 内循环增加 APPROVAL_PENDING 处理：

```python
if transition not in (Transition.APPROVAL_GRANTED, Transition.APPROVAL_REJECTED):
    if transition == Transition.APPROVAL_PENDING and not state.get("pending_approval"):
        break  # 没有待审批工具才退出
    # 否则继续，让 resolve() 处理
    continue
```

--- 

## Bug 2: `_emit_sse` 从 `tool_calls` 和 `_emitted_results` 发出重复 ToolCallStarted

**文件**: `web-server/src/agent/loop/orchestrator.py:274-352`

### 代码

```python
async def _emit_sse(self, state, counter, profiler, channel):
    # 第一遍: 从 tool_calls 发 ToolCallStarted
    for tc in state.get("tool_calls") or []:
        if tc.get("id") in pending_ids:
            continue
        await channel.send(ToolCallStarted(
            call_id=tc.get("id") or str(uuid4()),  # ← 使用 tool_calls 的 id
            ...
        ))

    # 第二遍: 从 _emitted_results 发 ToolCallStarted + ToolCallFinished
    emitted = state.get("_emitted_results") or state.get("streaming_tool_results") or []
    for sr in emitted:
        await channel.send(ToolCallStarted(
            call_id=sr.get("tool_call_id") or str(uuid4()),  # ← 使用结果中的 tool_call_id
            ...
        ))
        await channel.send(ToolCallFinished(
            call_id=sr.get("tool_call_id") or str(uuid4()),
            execution_status=...,
        ))
```

### 问题

每个工具**收到两次 ToolCallStarted**（第一次从 `tool_calls`，第二次从 `_emitted_results`），然后才收到 ToolCallFinished。前端响应：

```typescript
on_tool_call(data) {           // 第一次: tool_call
    agentPhase.value = 'calling_tool'  // ← 重置为 calling_tool
    toolCalls.value = new Map(...)     // RUNNING
}
on_tool_call(data) {           // 第二次: tool_call（重复）
    agentPhase.value = 'calling_tool'  // ← 再次重置！
    toolCalls.value = new Map(...)     // RUNNING（覆盖同值）
}
on_tool_result(data) {         // tool_result
    agentPhase.value = 'thinking'
    toolCalls.value = new Map(...)     // SUCCEEDED
}
```

在单次 js 微任务内，Vue 批处理会合并这些更新，最终状态是 SUCCEEDED。**但如果事件跨越微任务边界**（由于 SSE parser 的异步调度），`agentPhase` 可能短暂停留在 `'calling_tool'`。

这可能不是 "Running…" 的直接原因，但**会使前端 tool_call 数量翻倍**，增加不必要的状态更新。

### 修复方向

`_emit_sse` 中的 `_emitted_results` 路径只应发出 ToolCallFinished，不应再发 ToolCallStarted。工具已在 `tool_calls` 路径中被 announce。

---

## Bug 3: `route_after_review` 提前结束图

**文件**: `web-server/src/agent/nodes/observe.py:66-70`

### 代码

```python
def route_after_review(state) -> str:
    if state.get("pending_approval"):
        return "__end__"    # ← 直接结束！
    return "act"
```

### 问题

当 `review_node` 返回混合结果（部分自动批准、部分需审批）时，`pending_approval` 非空导致图直接结束，**自动批准的工具永远不会在当前图中执行**。它们要等到审批循环处理完 pending 的工具后，在下一轮 graph.ainvoke 中才被执行。

这导致：
1. 自动批准的工具的 `ToolCallStarted` 延迟发出（等 pending 工具批完）
2. 用户体验上，工具"等待"了不必要的时间
3. 如果审批循环因 Bug 1 提前退出，自动批准的工具永远不执行

### 修复方向

```python
def route_after_review(state) -> str:
    if state.get("approved_tool_calls"):
        return "act"          # ← 先执行已批准的
    if state.get("pending_approval"):
        return "__end__"
    return "act"
```

这样 pending 的工具等待审批，但 auto-approved 的工具立即执行。

---

## Bug 4: 前端 `on_error` 不清理 RUNNING 工具

**文件**: `frontend/vue-project/src/composables/useSessionManager.ts:371-378`

### 代码

```typescript
on_error(data: any) {
    isStreaming.value = false
    agentPhase.value = 'idle'
    connectionError.value = {
        code: data.code || 'UNKNOWN',
        message: data.message || String(data),
    }
    // ❌ 未清理 toolCalls 中 RUNNING 的条目
},
```

对比 `on_done`（line 342-358）中明确清理 RUNNING 工具的逻辑：

```typescript
on_done(_data: any) {
    // ...
    const updated = new Map(toolCalls.value)
    for (const [id, tc] of updated) {
        if (tc.execution_status === 'RUNNING') {
            updated.set(id, { ...tc, execution_status: 'FAILED', ... })
        }
    }
    toolCalls.value = updated
},
```

### 影响

当 SSE 流以 `error` 事件结束时（如 `AGENT_CRASH`、`TURN_LIMIT_EXCEEDED`、`TOKEN_BUDGET_EXCEEDED`），前端：
1. 收到 `error` 事件 → `on_error` 运行 → 不清理工具 → 工具停留在 "Running…"
2. 然后 `done` 事件（SSEStream 的 `finally`） → `on_done` 运行 → 清理工具 → 显示 "Failed"

**但** `done` 事件是否总是在 `error` 之后？看 `sse_stream.py`：

```python
try:
    async for event in chat_turn.events():
        yield _to_wire(event)
except Exception:
    yield {"event": "error", "data": json.dumps({...})}
finally:
    yield {"event": "done", "data": "{}"}  # ← 无条件！
```

`done` 在 `finally` 中，确实在 `error` 之后。前端 `on_done` 会清理 RUNNING 工具。**所以 Bug 4 本身不是 "Running…" 的直接原因，但它是防御性编程的缺失。**

在极少数情况下，如果 `error` 事件发送后 SSE 连接在 `done` 发送前中断，前端会停留在 `on_error` 状态，工具显示 "Running…"。

### 修复方向

```typescript
on_error(data: any) {
    isStreaming.value = false
    agentPhase.value = 'idle'
    connectionError.value = { code: data.code || 'UNKNOWN', message: data.message || String(data) }
    // 清理 RUNNING 工具（同 on_done）
    const updated = new Map(toolCalls.value)
    for (const [id, tc] of updated) {
        if (tc.execution_status === 'RUNNING') {
            updated.set(id, { ...tc, execution_status: 'FAILED', error: { message: 'Connection closed before tool completed' } })
        }
    }
    toolCalls.value = updated
},
```

---

## Bug 5: 数据库 `executed_tool_list` 中 `call_id` 为 None

**文件**: `frontend/vue-project/src/composables/useSessionManager.ts:462-467`
**证据**: 之前调查中发现 `executed_tool_list` 记录的 `call_id` 和 `tool_name` 为 `null`

### 问题

`loadHistory()` 将 `executed_tool_list` 加载到 `toolCalls` Map 中：

```typescript
if (session.executed_tool_list) {
    for (const tc of session.executed_tool_list) {
        map.set(tc.call_id, tc)   // ← call_id 为 null → key 为 null
    }
}
toolCalls.value = map
```

如果 `call_id` 为 `null`，Map key 就是 `null`。后续的 `get(null)` 能查到，但**前端渲染时**如果遇到 `execution_status='SUCCEEDED'` 应该显示 "Done"，这不应该直接导致 "Running…"。

**根因**: 数据库 `executed_tool_list` 的 `call_id` 和 `tool_name` 在保存时丢失：

```python
# web-server/src/agent/loop/audit.py 或类似位置
# 往 executed_tool_list 写入时，call_id 和 tool_name 字段为 None
```

### 影响

- 加载历史会话时，工具记录的 `call_id` 为 `null`
- `tool_name` 为 `null` → ToolCallInline 显示空名称
- `execution_status` 可能是 `SUCCEEDED` 或 `RUNNING`（取决于保存时的时序）
- 如果 `execution_status='RUNNING'` 被持久化，历史会话会显示永久 "Running…"

---

## Bug 6: `ALASCUP_DEBUG` 环境变量不匹配

**文件**: `web-server/src/observability/debug_log.py:17`

```python
_enabled: bool = os.environ.get("ALASCUP_DEBUG", "").strip() == "1"
```

启动命令设的是 `ALASCUP_DEBUG=DEBUG`，但代码检查的是 `== "1"`。所以 `debug_log` 的**文件写入和过滤功能未启用**，只用了内存环形缓冲区（500行）。这导致调试日志无法写入文件。

### 影响

- `_emit_sse` 中的 `logger.debug(...)` 行写入 loguru 日志（已正确记录）
- `debug_log("DEBUG", "...")` 调用只写入环形缓冲区，不写入文件
- `ALASCUP_DEBUG_LEVEL` 也设为 `"DEBUG"`，但 `_enabled=False` 导致所有调用都跳过文件写入

---

## Bug 7: `classify_companion` 兼容性问题（加剧因素）

**文件**: `web-server/src/mcp_client/executor.py`

`classify_companion` 调用 `{tool}_classify` 时因 `timeout` 参数不兼容而失败：

```
fastmcp.exceptions.ToolError: Unexpected keyword argument timeout
```

虽然后备逻辑正确（`except Exception: classification = {"safe": False}`），但这导致**所有可变工具都被标记为不安全**，必须人工审批。使得 "Running…" bug 更容易触发（审批越多，遇到 Bug 1 的概率越高）。

---

## 修复优先级

| 优先级 | Bug | 文件 | 影响 |
|--------|-----|------|------|
| 🔴 P0 | 1: `clear_transient_fields` 清 `pending_approval` | `transitions.py:9-16` | 待审批工具丢失 |
| 🔴 P0 | 5: `executed_tool_list` 的 `call_id=None` | 后端持久化逻辑 | 历史会话显示异常 |
| 🟡 P1 | 4: `on_error` 不清理 RUNNING | `useSessionManager.ts:371-378` | 异常中断后残留 "Running…" |
| 🟡 P1 | 2: 重复 ToolCallStarted | `orchestrator.py:320-330` | 前端状态多余翻转 |
| 🟢 P2 | 3: `route_after_review` 提前结束 | `observe.py:66-70` | 自动批准工具延迟执行 |
| 🟢 P2 | 6: `ALASCUP_DEBUG` 环境变量 | `debug_log.py:17` | 开发体验 |
| 🟢 P2 | 7: `classify_companion` 兼容性 | `executor.py` | 所有可变工具需审批 |

## 受影响文件汇总

| 文件 | 行 | Bug |
|------|-----|-----|
| `web-server/src/agent/loop/transitions.py` | 9-16 | 1: `pending_approval` 被清除 |
| `web-server/src/agent/loop/orchestrator.py` | 274-352 | 2: 重复 ToolCallStarted |
| `web-server/src/agent/nodes/observe.py` | 66-70 | 3: `route_after_review` 提前结束 |
| `frontend/.../useSessionManager.ts` | 371-378 | 4: `on_error` 不清理 |
| 后端持久化逻辑（待定位） | — | 5: `call_id=None` |
| `web-server/src/observability/debug_log.py` | 17 | 6: 环境变量不匹配 |
| `web-server/src/mcp_client/executor.py` | — | 7: `classify_companion` 兼容性 |

## 验证方法

每个 Bug 修复后，通过以下方式验证：

1. **Bug 1 fix**: 新建会话 → 发送"总结今天的系统日志" → 连续批准 3+ 次 → 检查最终回复是否生成、所有工具是否显示 Done
2. **Bug 2 fix**: 检查 SSE 日志中每个工具是否只有一次 ToolCallStarted
3. **Bug 4 fix**: 在工具执行时断开网络 → 重新连接 → 检查工具是否显示 Failed
4. **Bug 5 fix**: 加载历史会话 → 检查工具记录是否包含正确的 call_id 和 tool_name
