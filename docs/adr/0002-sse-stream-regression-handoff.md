# Handoff: SSEStream 引入了三項回歸，原 bug 未解決

Date: 2026-06-08

## 目標任務

1. 修復審批後工具卡仍顯示 "Running…"
2. 修復 `X-Session-ID` 返回 `"new"` 而非真實 UUID
3. 不引入回歸

## 已完成的改動（五個 slice）

| Slice | 描述 | 狀態 |
|-------|------|------|
| 1 | `web-server/src/sse_stream.py` 新增 `SSEStream` 類 | ✅ |
| 2 | `web-server/src/api/chat.py` 改用 `SSEStream`，移除 `X-Session-ID` header | ✅ |
| 3 | 前端 `useSessionManager.ts` 監聽 `session_init` 事件 | ✅ |
| 4 | `events.py:82` `is_read_only` 不再硬編碼 → `sr.get("is_read_only", False)` | ✅ |
| 5 | `web-server/tests/unit/test_sse_stream.py` 新增 14 測試 | ✅ |

## 引入的三項回歸

### 回歸 1：推理（reasoning）事件完全消失

**現象**：SSE 流不再輸出 `reasoning` 事件。

**根因**：`SSEStream.__aiter__()` 直接同步調用 `self._query.run()`，該方法只 yield `LoopOrchestrator._emit_sse()` 的**最終事件**。原始 `Query.chat()` 中的 `drain_queue` 任務負責把 `_event_queue` 中的即時 LLM 推理字元轉發到 `event_queue`，被完全遺漏。

```python
# sse_stream.py:140 —— 遺漏了 drain_queue
async for event in self._query.run(messages, available_tools, system=system_prompt):
    yield event
```

`_event_queue` 雖然在 sse_stream.py:138-139 被創建和設為 ContextVar，但沒有任何 task 去讀取它。

### 回歸 2：流式 assistant delta 消失

**現象**：assistant 回覆不再逐字輸出，只收到一條最終文本。

**根因**：同上。原始 `Query.chat()` 的 `drain_queue` 同時轉發 reasoning 和 assistant delta，`SSEStream` 完全沒有對等的並行讀取機制。LLM think_node 通過 `_forward_to_queue` 發出的 `assistant` delta 事件寫入 `_event_queue`，無人消費。

### 回歸 3：原 bug "Running…" 未解決

**現象**：審批後工具仍顯示 "Running…"。

**可能根因**（待進一步驗證）：原始 bug 和 `_event_queue` / `drain_queue` 的缺失沒有直接關係——`_emit_tool_results` 在 `_emitted_results` 中的確會發出配對的 `tool_call`+`tool_result`，`message_id` 一致。但缺少即時流可能導致前端在審批後重新初始化 session state，造成狀態丟失。

## 當前 SSE 流形態

### 修改前（Query.chat() 正確行為）

```
reasoning: {"delta": "用户让..."}
reasoning: {"delta": "我运行..."}
thinking_done: {}
assistant:  {"delta": "正在"}       ← 逐字流式
assistant:  {"delta": "执行..."}    ← 逐字流式
assistant:  {"chat_id":..., "message_id":..., "delta": "完整文本"}  ← 最終
tool_call:  {message_id: "call_00_...", tool_name: "bash"}
tool_result:{message_id: "call_00_...", execution_status: "SUCCEEDED"}
done: {}
```

### 修改後（SSEStream 錯誤行為）

```
session_init: {"chat_id": "..."}   ← 新增，正確
assistant:    {"chat_id":..., "message_id":..., "delta": "完整文本"}  ← 只有最終
done: {}
```

缺：reasoning、流式 assistant delta、thinking_done。審批場景中 tool_call+tool_result 理論上會出現在 assistant 和 done 之間（來自 `_emit_sse`），但未通過審批流程驗證。

## 修復方向

### 必須在 SSEStream 中重建 `drain_queue` 機制

`SSEStream.__aiter__()` 需要複製 `Query.chat()` 的並行任務模式：

```python
async def __aiter__(self):
    # ... session setup ...
    queue = asyncio.Queue()
    event_queue = asyncio.Queue(maxsize=128)

    async def drain_queue():
        """即時轉發 _event_queue → event_queue（reasoning + assistant deltas）"""
        while True:
            item = await queue.get()
            if "data" not in item:
                item["data"] = "{}"
            await event_queue.put(("item", item))

    async def run_agent():
        """執行 agent，完成後發 done sentinel"""
        try:
            async for event in self._query.run(...):
                await event_queue.put(("agent", event))
        finally:
            await event_queue.put(("done", None))

    drain_task = asyncio.create_task(drain_queue())
    agent_task = asyncio.create_task(run_agent())
    agent_done = False

    try:
        while not agent_done:
            tag, event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
            if tag == "done":
                agent_done = True
                continue
            yield event
            if await self._is_disconnected():
                break

        # Drain remaining
        while not event_queue.empty():
            tag, event = event_queue.get_nowait()
            if event:
                yield event
    finally:
        agent_task.cancel()
        drain_task.cancel()
        # ...
```

### 可以不動 query.py / orchestrator.py

`Query.run()` / `LoopOrchestrator` 內部無需修改。`_event_queue` ContextVar 機制依然有效——think_node 繼續通過 `_forward_to_queue` 寫入 `_event_queue`，SSEStream 的 `drain_queue` 負責消費。

### chat.py 無需回退

`chat_turn` 使用 `SSEStream` 的設計保持不變。修正僅在 `SSEStream.__aiter__()` 內部。

## 測試現狀

| 文件 | 數量 | 狀態 |
|------|------|------|
| `test_sse_stream.py` | 14 | 全過（未測試流式行為） |
| `test_loop.py` | 18 | 全過 |
| `test_nodes.py` | 10 | 全過 |
| `test_query.py` | 10+ | 全過（使用 Query.run() 非 chat()） |
| 其他單元測試 | ~300 | 346 過，2 預存失敗 |

**缺失的測試**：沒有測試驗證 SSEStream 的即時流行為（reasoning deltas, assistant deltas）。現有測試只斷言最終事件序列。

## 下一個 agent 應該做什麼

1. **修復 SSEStream.__aiter__()**：引入 drain_queue + agent_task 並行模式（參考原始 `Query.chat()` 邏輯）
2. **新增流式行為測試**：測試 reasoning delta、assistant delta 按順序即時輸出
3. **通過審批流程驗證**：確認審批後 tool_result 正確更新前端狀態
4. **端到端驗證**：跑完整 SSE 流，確認事件序列與修改前一致（多了 session_init，其他不變）

## 關鍵文件路徑

```
web-server/src/sse_stream.py              ← 主要修復目標
web-server/src/api/chat.py                ← 使用 SSEStream，無需修改
web-server/src/agent/query.py:58-225      ← 原始流式實現參考（drain_queue 模式）
web-server/src/agent/loop/orchestrator.py ← 最終事件發射
web-server/src/agent/loop/events.py       ← _emit_tool_results（is_read_only 已修復）
web-server/src/agent/nodes/act.py         ← is_read_only 已傳入 tool_results
web-server/src/agent/nodes/observe.py     ← _emitted_results 保留 metadata
frontend/vue-project/src/types.ts         ← SessionInitEvent 類型新增
frontend/vue-project/src/composables/useSessionManager.ts ← on_session_init
```
