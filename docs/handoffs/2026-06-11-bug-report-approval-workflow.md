# Bug Report: 审批工作流在多步工具调用时卡死

## 摘要

Agent 审批工作流在「首次通过审批 → 执行工具 → LLM 生成新工具调用」的流程中断。第二次工具调用永远卡在 **"Running…"** 状态，最终回复不显示。用户需要刷新页面才能继续。

## 复现步骤

1. **前提**: 工具 `bash` 被规则引擎判定为危险（需要人类审批），或 `bash_classify` 返回不安全
2. 新建会话，发送消息：`总结今天的系统日志`
3. Agent 思考后生成 bash 命令（`ls -la /var/log/...`）
4. **审批弹窗出现** → 点击 **Approve**
5. ✅ 第一个工具执行完成，显示 **"Done"**
6. Agent 再次思考，生成第二个 bash 命令（`journalctl --since "2026-06-11"...`）
7. ❌ **前端显示 "Running…" 并永久卡死**
   - 审批弹窗未出现
   - 最终总结回复未出现
   - 输入框可用但 Send 按钮 disabled

## 实际 vs 预期行为

| 步骤 | 预期 | 实际 |
|------|------|------|
| 第一次审批 | 弹窗 → Approve | ✅ 正常 |
| 第一个工具 | 执行 → Done | ✅ 正常 |
| 第二个工具 (`journalctl`) | 审批弹窗或自动执行 → 结果 | ❌ 永久 "Running…" |
| 最终回复 | "总结今天的系统日志..." | ❌ 无 |
| SSE 流结束 | 正常 done | 收到 done，但状态残留在 "Running…" |

## 环境

- **web-server**: port 11450
- **session ID**: `38ee177c-c92c-4f98-a230-7fee46b2be9f`
- **Loop日志**: loop#1 在 14:04:53 完成（`duration_ms=12872.18`），**无 loop#2**

## 服务端日志（完整时间线）

```
14:04:41 feature_started loop#1
14:04:41 feature_started llm_call (第一次 LLM: 生成 ls 命令)
14:04:44 feature_completed llm_call (3.15s)
           ← 用户点击 Approve
14:04:50 feature_started tool_exec (执行 ls -la)
14:04:50 feature_completed tool_exec (33ms)
14:04:50 feature_started llm_call (第二次 LLM: 生成 journalctl 命令)
14:04:53 feature_completed llm_call (2.93s)
14:04:53 feature_completed loop#1 (12.87s) ← 循环在此退出
           ← 之后无任何日志
```

**关键**: loop 在第一次迭代后就退出了，没有处理第二次 LLM 调用产生的新工具调用。

---

## 根因分析

### Bug 1（Primary）: `_handle_transition` 未处理 `APPROVAL_PENDING` 状态

**文件**: `web-server/src/agent/loop/orchestrator.py` → `_handle_transition()`

#### 调用链

```
用户批准 -> orchestrator step 3.5: graph.ainvoke(state)
  ├─ think (fast-path: 看到 approved_tool_calls → 跳过 LLM)
  ├─ act (执行 ls -la)
  ├─ observe (组装结果)
  ├─ think (LLM 处理结果 → 生成 journalctl 工具调用)
  ├─ review (journalctl 被判定为危险 → pending_approval)
  └─ END (pending_approval → 图结束)
        ↑ state.pending_approval = [journalctl]
        ↑ state.transition = APPROVAL_PENDING

orchestrator step 5: _emit_sse(state)
  → 将 journalctl 作为 ToolCallStarted 发出（前端显示 "Running…"）

orchestrator step 6: clear_transient_fields(state)
  → 清除 tool_calls、pending_approval

orchestrator step 7: _handle_transition(APPROVAL_PENDING)
  → 不匹配任何 if 分支 → return None → loop 退出
```

#### 问题代码

```python
# _handle_transition (第364-382行)
def _handle_transition(self, state: dict) -> str | None:
    transition = get_transition(state)
    if transition == Transition.DONE:
        return "return"
    if transition in (Transition.TURN_LIMIT_EXCEEDED, Transition.TOKEN_BUDGET_EXCEEDED):
        return "return"
    if transition in (Transition.TOOL_RESULTS, Transition.APPROVAL_REJECTED):
        return "continue"
    if transition == Transition.APPROVAL_GRANTED:
        return "continue"
    return None  # ← APPROVAL_PENDING 走这里 → loop 退出
```

`APPROVAL_PENDING` 不在任何 if 分支中，走 `return None`。调用者：
```python
if action not in ("continue",):
    _finalize_iteration(profiler, loop_feature)
    return           # ← 循环退出！
```

#### 为什么这个bug不被早期发现

大部分单步工具调用场景不会触发此bug，因为：
- 只读工具（read-only）：直接在 think_node 预执行，不经过 review 审批
- 单步危险工具：一次审批 → 执行 → LLM 返回文本 → DONE → 正常结束
- **多步危险工具**（本场景）：第一次审批后 LLM 又生成新工具调用 → 触发此bug

#### 修复方向

在 `_handle_transition` 增加 `APPROVAL_PENDING` → `"continue"`，同时在 Step 3 周围增加循环以处理连续多轮审批。核心问题：Step 3.5（审批后重调用图）返回 `pending_approval` 后，应当**回退到 Step 3 再次进入审批循环**，而不是继续到 emit/cleanup/handle_transition。

```python
# 修复思路
# Step 3-3.5: 审批循环
while True:
    await self._approval.resolve(result, ...)
    state = result
    transition = get_transition(state)
    if transition not in (APPROVAL_GRANTED, APPROVAL_REJECTED):
        break
    result = await self._graph.ainvoke(state, self._config)
    if not result.get("pending_approval"):
        break
    # 有新的 pending_approval → 继续审批循环
```

---

### Bug 2: SSE 事件次序 — ToolCallStarted 早于审批

**文件**: `web-server/src/agent/loop/orchestrator.py`

`_emit_sse()`（Step 5）无条件将 state 中的 `tool_calls` 和 `_emitted_results` 作为 SSE 事件发出。当第二次 LLM 产生新工具调用时：

1. `tool_calls = [journalctl]`（未经过审批）
2. `_emit_sse` 发出 `ToolCallStarted(journalctl)` → **前端显示 "Running…"**
3. 然后 loop 因 Bug 1 退出
4. `ToolCallFinished(journalctl)` **永远不会发出**

正确次序应该为：先审批 → 再执行 → 再发出 ToolCallStarted/ToolCallFinished。

---

### Bug 3: 前端 SSE `on_done` 不清理残存 `RUNNING` 状态

**文件**: `frontend/vue-project/src/composables/useSessionManager.ts`

```typescript
on_done(_data: any) {
    agentPhase.value = 'done'
    isStreaming.value = false
    connectionError.value = null
    // 缺少: toolCalls 清理
    // toolCalls 中 execution_status='RUNNING' 的条目继续显示 "Running…"
},
```

即使后端正确结束 SSE 流，前端 toolCalls map 中仍为 `RUNNING` 状态的条目会被永久显示。应该：
- 将 `on_done` 中未收到结果的所有 `RUNNING` 状态工具调用标记为中断/错误
- 或者在 `on_done` 中重置/清理 toolCalls

---

## 补充发现：`classify_companion` 兼容性问题

**文件**: `web-server/src/agent/nodes/review.py` + `web-server/src/mcp_client/executor.py`

日志中发现 `classify_companion` 调用 `{tool}_classify` 时失败：

```
fastmcp.exceptions.ToolError: 1 validation error for call[<lambda>]
timeout
  Unexpected keyword argument [type=unexpected_keyword_argument, input_value=10, input_type=int]
```

虽然 `review.py` 第 58 行有 `classify_args = {k: v for k, v in args.items() if k != "timeout"}` 试图剥离 timeout 参数，但 FastMCP 客户端仍报 `timeout` 是非预期的参数。这会导致任意 `bash` 工具调用被标记为"危险"（必须人工审批）。可能与 FastMCP 3.x 客户端的 `call_tool` 签名变更有关。

`classify_companion` 失败后的 fallback：
```python
except Exception:
    classification = {"safe": False}  # 默认不安全
```

这个 fallback 是正确的（fail-closed），但如果 `classify_companion` 持续失败，所有 bash 调用都需要人工审批。

---

## 受影响文件清单

| 文件 | 行 | 问题 |
|------|-----|------|
| `web-server/src/agent/loop/orchestrator.py` | 364-382 | `_handle_transition` 缺 `APPROVAL_PENDING` → 提前退出 |
| `web-server/src/agent/loop/orchestrator.py` | 126-225 | `_run_loop` Step 3-3.5 缺少审批循环 |
| `web-server/src/agent/loop/orchestrator.py` | 227-301 | `_emit_sse` 在审批前发出 ToolCallStarted |
| `frontend/vue-project/src/composables/useSessionManager.ts` | 342-356 | `on_done` 不清理 RUNNING 状态 toolCalls |
| `web-server/src/mcp_client/executor.py` | 37-57 | `classify_companion` 兼容性问题 |

## 附注

- 本报告由端到端测试（Chrome DevTools + 真实服务）发现
- 测试命令：`总结今天的系统日志`
- 测试中使用了 Approve 按钮批准第一个工具
- 其他 bug（如 classify_companion）可能加剧此问题（导致更多工具需要审批）
