# ADR-0001: SSE stream session_init 事件替代 X-Session-ID header

## Status

Proposed (2026-06-08)

## Context

`chat.py` 在 SSE 流啟動前設置 `X-Session-ID` response header，但此時 session
尚未創建（session UUID 在 `Query.chat()` 內部異步生成），導致 header 值退
化為字面量 `"new"`。

後果：
- 前端會話列表出現 `chat_id: "new"` 的幽靈 session
- 頁面重載後 `GET /api/sessions/new` 返回 404，會話數據無法恢復
- 工具執行結果的 SSE 事件（`tool_result`）雖然正確發出，但因 session ID
  漂移，前端可能操作錯誤的 session state，表現為 "Running…" 持續不更新

## Decision

1. **引入 `SSEStream` 深模塊**，封裝 session 創建、agent 執行、SSE 事件發射、
   持久化、資源清理為一個 async iterable。

2. **第一條 SSE 事件為 `session_init`**，攜帶真實 `chat_id`。前端從此事件獲取
   session ID，不再依賴 `X-Session-ID` header。

3. **`chat_turn` 端點簡化**為僅構造 `SSEStream` 並傳給 `EventSourceResponse`。

4. **`_emit_tool_results` 硬編碼 `is_read_only: True` 修復**，改為從工具
   metadata 讀取.

## Consequences

- 前端需要监听 `session_init` 事件类型
- `X-Session-ID` header 可以移除（无需向后兼容）
- 现有测试框架（`_collect()` / MockLLM / MockExecutor）可以直接测试
  `SSEStream.__aiter__()` 的输出
- 错误处理、重试逻辑、SSE 协议细节都被封装在模块内部，外部接口保持不变
