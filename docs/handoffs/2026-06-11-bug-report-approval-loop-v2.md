# Bug Report: 审批循环修复后仍有2个阻塞问题

## 摘要

第一轮修复（`_handle_transition` 增加 `APPROVAL_PENDING` + 内审批循环）使多轮审批能够运行**3轮**，但仍有2个阻塞问题导致最终回复未能生成：

1. **`clear_transient_fields` 过早清空 `pending_approval`**（Primary）
2. **内循环 ToolCallFinished 发射查空 `tool_results`**（被 observe_node 清空过）

## 建议优先级

```
Bug 1 (clear_transient_fields) → 最终回复不生成
Bug 2 (ToolCallFinished) → 前端工具状态永远"Running…"
→ Bug 1 解决后 Bug 2 可能也解决（最终summary发出，_emit_sse 覆盖）
```

---

## Bug 1: `clear_transient_fields` 清空 `pending_approval` 导致审批遗漏

**文件**: `web-server/src/agent/loop/orchestrator.py:233`

### 调用链

```
内审批循环 第3轮迭代:
  [top] resolve(result)  → 等待审批 → transition = APPROVAL_GRANTED
  → emit ToolCallStarted(第3批工具)
  → graph.ainvoke(state)
       think(fast-path) → act(执行工具) → observe 
       → think(LLM处理结果)
       → LLM生成了文本 + 第4批工具(grep错误)
       → review → pending_approval
       → END(transition=APPROVAL_PENDING)
  → state = result  (pending_approval=[第4批], transition=APPROVAL_PENDING)
  
  → emit ToolCallFinished ← 但 tool_results=[]（被 observe 清过），实际没发出

  → state.get("pending_approval") → 非空 → 不break
  
  → clear_transient_fields(state)  【BUG开始】
      → state["pending_approval"] = []   ← 清掉了！
      → state["tool_calls"] = []
      → ...

  → 回到 while True 顶部:
  → resolve(result)  
      → result.get("pending_approval")  == []  → 立即return，啥也不做
  
  → transition = get_transition(state)  
      → 仍然是 APPROVAL_PENDING（review_node设置的，clear_transient_fields不清transition）
  
  → if transition not in (APPROVAL_GRANTED, APPROVAL_REJECTED):  → True
  → break  ← 内循环退出！第4批pending_approval从未被resolve处理！
```

### 根因

`clear_transient_fields` 在第233行清空了 `pending_approval`，但下一轮内循环的 `approval.resolve()` 需要 `state.get("pending_approval")` 为真才能工作。

| 变量 | clear_transient_fields 清 | resolve 需要 |
|------|--------------------------|--------------|
| `pending_approval` | ❌ 清成 `[]` | ✅ 非空才会处理 |
| `tool_calls` | ❌ 清成 `[]` | 不重要 |
| `transition` | ✅ 不清 | 不重要 |

同时 `transition`（`APPROVAL_PENDING`）没有被清理，导致第183行的 `transition not in (APPROVED, REJECTED)` 为 True → break。

### 后端日志证据

```
14:32:31 tool_exec #2 (73ms)          ← 第3批工具执行（dmesg）
14:32:31 llm_call #3 (4.7s, success)  ← LLM处理结果，生成第4批工具
14:32:36 loop#1 completed (91s)       ← 内循环break，外循环_emit_sse
14:32:36 loop#2 started               ← _handle_transition(APPROVAL_PENDING) → continue
14:32:36 llm_call (193ms, error)      ← Loop#2 LLM调用失败
14:32:36 loop#2 completed (0.2s)      ← 流结束
```

Loop#1 完成时 `pending_approval` 已被清空，但 `transition=APPROVAL_PENDING` 导致外循环继续 → Loop#2启动 → LLM错误（193ms内失败，可能因为state已被清除的残留）。

### 会话数据

通过 API 查询 `b8dd3af5` 会话:
- 3 条 assistant 消息（中间思考文本）
- **无最终总结消息**
- 13 个工具记录: 9 SUCCEEDED + 4 PENDING_APPROVAL（第4批）

### 修复方向

**方案A**: 不调用 `clear_transient_fields`，或跳过 `pending_approval`：

```python
# 方案A：只清工具结果，保留 pending_approval
for key in TRANSIENT_FIELDS:
    if key != "pending_approval":
        state[key] = []
```

**方案B**: 保存后再恢复：

```python
_pending = state.get("pending_approval", [])
clear_transient_fields(state)
state["pending_approval"] = _pending
```

**方案C（推荐）**: 重构内循环，让 `approval.resolve` 每次都在循环顶部自然处理 pending_approval，根本不需要手动 clear：

```python
while True:
    await self._approval.resolve(result, ...)
    ...
    if not state.get("pending_approval"):
        break
    # 不需要 clear_transient_fields — resolve 下一轮会覆盖
```

---

## Bug 2: 内循环 ToolCallFinished 查空 `tool_results`

**文件**: `web-server/src/agent/loop/orchestrator.py:212-228`

### 根因

`observe_node` 在 graph 内部运行时清空了 `tool_results`：

```python
# src/agent/nodes/observe.py:36-43
def observe_node(state, *, tool_results=None):
    ...
    return {
        "messages": tool_messages,
        "streaming_tool_results": [],
        "tool_results": [],           # ← 清空！
        "rejected_tool_calls": [],
        "_emitted_results": results,  # ← 结果在这里
        "transition": Transition.TOOL_RESULTS,
    }
```

但内循环在第212行读的是 `state.get("tool_results")`：

```python
# 内循环 emit ToolCallFinished
for r in (state.get("tool_results") or []):  # ← 永远是 []
    ...
    await channel.send(ToolCallFinished(...))
```

所以 **ToolCallFinished 永远不会从内循环发出**。实际结果在 `_emitted_results` 中，等到外循环的 `_emit_sse`（第265行）才发出：

```python
# _emit_sse 第311行
emitted = state.get("_emitted_results") or ...
for sr in emitted:
    tcs = ToolCallStarted(...)  # 重新发出 ToolCallStarted（重复！）
    ...
    tcf = ToolCallFinished(...) # 真正发出 ToolCallFinished
```

### 影响

- ToolCallStarted 从内循环发 → 前端设 `RUNNING`
- ToolCallFinished 延迟到外循环 `_emit_sse` 从 `_emitted_results` 发 → 如果外循环正常结束，会收到
- 但如果外循环提前退出（Bug 1导致Loop#2失败），`_emit_sse` 可能不执行 → 前端永久"Running…"
- 另外 `_emitted_results` 发出重复的 ToolCallStarted（call_id 相同），前端设回 `RUNNING`，然后 ToolCallFinished 才覆盖

### 修复方向

内循环 emit ToolCallFinished 时，应从 `_emitted_results` 读取而非 `tool_results`：

```python
# 修复：从 _emitted_results 读取
for r in (state.get("_emitted_results") or state.get("streaming_tool_results") or []):
    res = r.get("result", {})
    if isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
        res = res["result"]
    await channel.send(ToolCallFinished(
        call_id=r.get("tool_call_id") or str(uuid4()),
        execution_status=res.get("execution_status", "SUCCEEDED"),
        output=res.get("output"),
        error=res.get("error"),
        execution_time_ms=res.get("execution_time_ms"),
    ))
```

**注意**: 这样修后，需要确保外循环的 `_emit_sse` 不再从 `_emitted_results` 重复发送，否则会产生重复事件。

---

## 复现步骤（验证修复后仍存在）

1. 启动服务：`web-server:11450`, `tool-server:11451`, `frontend:5173`
2. 输入 `总结今天的系统日志`
3. 连续3次 Approve
4. 预期：最终总结回复
5. 实际：中间思考文本出现，最终回复缺失，前端工具状态飘在"Running…"

## 修复验证方式

手工测试（同复现步骤），检查：
- 最终总结回复是否正确显示
- 工具是否显示 "Done" 而非 "Running…"
- 会话历史包含完整的 assistant 总结

## 受影响的文件

| 文件 | 行 | 问题 |
|------|-----|------|
| `web-server/src/agent/loop/orchestrator.py` | 233 | `clear_transient_fields` 清 `pending_approval` → 审批遗漏 |
| `web-server/src/agent/loop/orchestrator.py` | 212-228 | `tool_results` 为空 → ToolCallFinished 不发射 |
| `web-server/src/agent/loop/orchestrator.py` | 311-341 | `_emitted_results` 重复发 ToolCallStarted（次要） |
