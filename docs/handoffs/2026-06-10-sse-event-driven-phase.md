# Handoff: SSE事件驱动的状态显示 + 架构评估

## Summary

Grill session讨论了Chat UI phase标签从setTimeout驱动改为SSE事件驱动。发现了架构根因：`graph.ainvoke()`阻塞模式 + 手动_queue的张力。决定新增`tool_executing`事件作为第一步，去LangGraph化留待将来。

## Current State

- **PRD**: https://github.com/nazzeDe/alascup-agent/issues/19 (Chat UI improvements)
- **Branch**: `main` (未提交改动)
- **问题**: phase标签用800ms setTimeout做calling_tool→waiting_for_tool转换，不准确

## Architecture Discovered

```
SSEStream → Query → LoopOrchestrator → graph.ainvoke(state)
                  ├─ queue (实时流式delta from think_node)
                  └─ _emit_sse (事后发射 tool_call/tool_result)
```

**根本问题**: `graph.ainvoke()`是阻塞调用，graph内部事件走queue，graph结束后`_emit_sse`再读state发事件。两者到达前端的顺序不可控。

**为什么不用LangGraph的`get_stream_writer()`**: 因为用了`ainvoke()`而非`astream()`。`get_stream_writer()`只支持stream模式。

## Decisions Made

1. **Phase驱动方式**: 用SSE事件，不加前端setTimeout
2. **新事件名称**: `tool_executing`，在act_node执行前发射
3. **发射位置**: act_node内，通过`config.configurable._event_queue`发送
4. **事件数据**: 带`tool_name`、`message_id`、`server_name`
5. **前端Phase**: 新增"正在运行工具…"状态
6. **架构评估**: LangGraph对此项目价值有限——4个节点顺序调用，不需要DAG/状态机。但拆的成本（20文件/140测试）不划算。分两步走。

## Plan

### Step 1 (当前): 最小改动加tool_executing
- act_node: 执行工具前emit `tool_executing`到queue
- 前端: 收到tool_executing → phase = "正在运行工具…"
- 前端: 删800ms setTimeout
- events.py: 不动

### Step 2 (将来): 去LangGraph化
- 4个async函数替代graph
- 事件直接yield，顺序天然正确
- 减少依赖和config传播复杂度

## Files Involved

- `web-server/src/agent/nodes/act.py` — 加tool_executing发射
- `web-server/src/agent/loop/events.py` — 可能需要加emit helper
- `frontend/vue-project/src/composables/useSessionManager.ts` — 新phase + 删setTimeout
- `frontend/vue-project/src/types.ts` — AgentPhase新增'executing'

## Tests

- 单元测试: act_node emit正确事件
- 集成测试: SSE流包含tool_executing事件
- 前端测试: phase状态转换

## Suggested Skills for Next Session

- `superpowers:verification-before-completion`
- `code-review`
