# DeepSeek API Format Requirements

> Based on [DeepSeek Official API Docs](https://api-docs.deepseek.com/) and empirical testing.
> This document defines the **invariants** that our LLM adapter must satisfy when calling DeepSeek.

## Endpoint

```
POST https://api.deepseek.com/chat/completions
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | yes | `deepseek-v4-pro`, `deepseek-v4-flash`, etc. |
| `messages` | array | yes | Message list (see below) |
| `stream` | boolean | no | Default `false`. Enable SSE streaming. |
| `max_tokens` | integer | no | Valid range `[1, 393216]` for v4 models |
| `tools` | array | no | Tool definitions for function calling |
| `temperature` | number | no | `[0, 2]`, default `1.0` |
| `top_p` | number | no | `[0, 1]`, default `1.0` |
| `response_format` | object | no | `{"type": "json_object"}` for JSON mode |

## Message Format

### System Message

```json
{"role": "system", "content": "You are a helpful assistant."}
```

### User Message

```json
{"role": "user", "content": "Hello"}
```

### Assistant Message (plain text)

```json
{"role": "assistant", "content": "Hi there!"}
```

### Assistant Message (with reasoning — REQUIRED for deepseek-v4-flash)

```json
{
  "role": "assistant",
  "content": "The answer is 42.",
  "reasoning_content": "Let me think step by step..."
}
```

> **CRITICAL**: `deepseek-v4-flash` returns `reasoning_content` in every response.
> When building multi-turn conversations, the assistant message **MUST** include
> `reasoning_content` from the previous response. Omitting it causes:
> ```
> {"error": {"message": "The `reasoning_content` in the thinking mode must be passed back to the API."}}
> ```

### Assistant Message (with tool calls)

```json
{
  "role": "assistant",
  "content": "Let me check the weather.",
  "reasoning_content": "User wants weather info, I should call get_weather.",
  "tool_calls": [
    {
      "id": "call_xxxx",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"location\": \"Beijing\"}"
      }
    }
  ]
}
```

**Rules:**
- `content` may be `null` or empty string when only tool calls are generated
- `reasoning_content` MUST be preserved from the original response
- `tool_calls[].function.arguments` must be a **JSON string**, not an object
- `tool_calls[].type` must be `"function"`
- `tool_calls[].id` must match the `tool_call_id` in subsequent tool messages

### Tool Message (function result)

```json
{
  "role": "tool",
  "tool_call_id": "call_xxxx",
  "name": "get_weather",
  "content": "{\"temp\": 25, \"condition\": \"sunny\"}"
}
```

**Rules:**
- `tool_call_id` must match the `id` from the corresponding `tool_calls` entry
- `content` must be a string (serialize objects as JSON)
- `name` should match the function name

## Tools Definition

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string", "description": "City name"}
          },
          "required": ["location"]
        }
      }
    }
  ]
}
```

**Rules:**
- `type` must be `"function"`
- `parameters` must be a valid JSON Schema object
- `parameters.type` must be `"object"`

## Streaming Format (SSE)

When `stream: true`, the response is Server-Sent Events:

```
data: {"id":"...","choices":[{"delta":{"role":"assistant"}}]}
data: {"id":"...","choices":[{"delta":{"reasoning_content":"Let me"}}]}
data: {"id":"...","choices":[{"delta":{"reasoning_content":" think..."}}]}
data: {"id":"...","choices":[{"delta":{"content":"The answer"}}]}
data: {"id":"...","choices":[{"delta":{"content":" is 42."}}]}
data: {"id":"...","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

**Rules:**
- Each line starts with `data: ` (note the space)
- Last line is `data: [DONE]`
- `reasoning_content` chunks come BEFORE `content` chunks
- Empty delta `{}` signals end of generation (with `finish_reason`)

## Error Response

```json
{
  "error": {
    "message": "The `reasoning_content` in the thinking mode must be passed back to the API.",
    "type": "invalid_request_error",
    "code": "invalid_request_error"
  }
}
```

HTTP status codes: `400` (bad request), `401` (auth), `429` (rate limit), `500`+ (server error).

## Multi-Turn Conversation Invariants

When constructing a multi-turn conversation, these rules MUST hold:

1. **reasoning_content preservation**: Every assistant message that originally contained
   `reasoning_content` MUST include it in subsequent requests.
2. **tool_call_id matching**: Each tool message's `tool_call_id` must match the
   corresponding assistant `tool_calls[].id`.
3. **Message ordering**: system → user → (assistant → tool)* → assistant
4. **No orphan tool messages**: A tool message must follow an assistant message
   with a matching tool_call.
5. **arguments is string**: `tool_calls[].function.arguments` is always a JSON-encoded
   string, never a raw object.
