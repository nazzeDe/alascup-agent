# Bug: LLM 发送 "查看今天的系统日志" 后无响应

Date: 2026-06-11
Session: E2E verification of architecture deepening (#24–#30)
Found by: chrome-devtools E2E test + server log inspection

## Symptom

User sends "查看今天的系统日志". Agent starts working, then gets stuck.
- Some bash commands show "Running…" forever in UI
- One bash command stuck on approval prompt
- No final assistant response delivered

## Root cause

**`classify_companion` passes ALL tool args to `{tool}_classify` companion, but companion's schema doesn't include `timeout`.**

The LLM generates bash tool calls with a `timeout` param (the bash tool-server function accepts it):
```json
{"command": "journalctl --since today --no-pager 2>&1 | tail -200", "timeout": 15}
```

Flow:
1. `review.py:59` → `executor.classify_companion("bash", {command, timeout}, server)`
2. `executor.py:47-48` → `client.call_tool("bash_classify", {command, timeout})`
3. Tool-server `main.py:292` → companion registered as `lambda command="": fn(command)` — ONLY accepts `command`, no `timeout`
4. FastMCP validates args against schema → **rejects `timeout`** → raises `ToolError`

**Log evidence:**
```
fastmcp.exceptions.ToolError: 1 validation error for call[<lambda>]
timeout
  Unexpected keyword argument [type=unexpected_keyword_argument, input_value=15, input_type=int]
```

This is followed by the review node's fallback:
```
WARNING | src.agent.nodes.review:_classify_tool_call:61
  classify_companion failed for tool=bash, treating as dangerous
```

Classification returns `safe=False` (fail-closed design) → all bash calls forced to human approval → workflow stuck.

## Code locations

- **`web-server/src/mcp_client/executor.py:47-48`** — `classify_companion()` passes raw `params` dict to `client.call_tool(companion_name, params)`. No filtering of args that the companion doesn't accept.
- **`tool-server/src/main.py:291-292`** — `_COMPANION_FACTORY["bash"] = lambda fn: lambda command="": fn(command)` — bash_classify only takes `command`. **FastMCP doesn't support `**kwargs`**—tried that, got `ValueError: Functions with **kwargs are not supported as tools`.
- **`web-server/src/agent/nodes/review.py:58-59`** — caller site: `classification = await executor.classify_companion(name, args, tc.get("server_name", ""))` where `args` is the full tool arguments dict.
- **`web-server/src/mcp_client/registry.py:91-93`** — `_fetch_tools()` strips hidden tools (including classify companions) from public cache, so registry-based schema lookup won't find them.

## Suggested fix approaches

### A. Filter params in `classify_companion` by companion's input schema (recommended)

In `executor.py:classify_companion`:
1. Before calling companion, `await client.list_tools()` to find companion's `inputSchema`
2. Filter `params` to only include keys the companion schema accepts
3. Call companion with filtered params

Companion tools are hidden (excluded from registry), but `client.list_tools()` called directly returns ALL tools including hidden ones.

```python
companion = f"{tool_name}_classify"
async with Client(url) as client:
    tools = await client.list_tools()
    schema = {}
    for t in tools:
        if t.name == companion:
            schema = getattr(t, "inputSchema", None) or {}
            break
    allowed = set(schema.get("properties", {}).keys())
    filtered = {k: v for k, v in params.items() if k in allowed} if allowed else params
    result = await client.call_tool(companion, filtered)
```

Adds one `list_tools()` round-trip per classified tool. Companion tools are on same server (same client connection) so negligible overhead.

### B. Strip `timeout` in review_node before passing to classify

In `review.py:_classify_tool_call`, strip known non-classification params before calling `classify_companion`:
```python
classify_args = {k: v for k, v in args.items() if k != "timeout"}
classification = await executor.classify_companion(name, classify_args, server_name)
```

Fragile—other tools may add more non-classification params.

### C. Split companion call args from tool args in `_tool_dispatch.py`

In `_tool_dispatch.py:_classify_and_route`, separate args that are classification-relevant (e.g. `command`) from execution-only params (e.g. `timeout`). Tag only relevant ones for classify. More invasive.

## Test to reproduce

1. Start web-server + tool-server + frontend
2. Open Chrome at `http://localhost:5173`
3. Send "查看今天的系统日志" (or any prompt that triggers `bash` with `timeout`)
4. Observe: bash commands stuck at "Running…" or approval prompt
5. Check web-server log: `fastmcp.exceptions.ToolError ... timeout ... Unexpected keyword argument`

## Related files

- `web-server/src/mcp_client/executor.py` — `classify_companion()` (line 37-57)
- `tool-server/src/main.py` — `_COMPANION_FACTORY` (line 291-292)
- `web-server/src/agent/nodes/review.py` — `_classify_tool_call()` (line 52-66)
- `tool-server/src/security/bash_classify.py` — `classify_bash(command: str)` (line 150)
