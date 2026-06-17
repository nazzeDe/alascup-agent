# API 规范与数据契约

位置：`docs/api-spec/`

包含：

- `openapi.yaml`：OpenAPI 3.0 草案，定义 web-server REST 端点、SSE 事件和数据模型
- `schemas/`：JSON Schema 文件，用于前后端和测试校验

说明：契约以 `web-server/src/api/` 和 `web-server/src/sse_stream.py` 的当前实现为准；OpenAPI 中的 `SSEEvent` 描述的是 SSE event/data 的 wire 结构。
