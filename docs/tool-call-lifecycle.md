# Tool Call Lifecycle Invariants

The web-server uses three IDs for tool calls. They are intentionally separate.

- `llm_tool_call_id`: canonical ID for LLM history and SSE `tool_call` / `tool_result` pairing.
- `call_id`: database row UUID. Persistence handle only.
- `request_id`: human approval request ID. Approval bridge, approval API, and audit only.

The Tool Call Lifecycle module owns persistence state transitions:

- approval granted -> `APPROVED` / `RUNNING`
- approval rejected -> `REJECTED` / `FAILED`
- approval expired -> `EXPIRED` / `FAILED`
- execution result -> execution status, error, backup ref, result payload

Agent nodes report facts or intent. They should not encode persistence state transitions directly.

Rejected and expired approvals have two representations:

- database lifecycle: `approval_status=REJECTED|EXPIRED`, `execution_status=FAILED`
- SSE / LLM / UI: a matching `tool_result` with `execution_status=REJECTED`

That rejected `tool_result` is still a real tool message for LLM history. It tells the model not to retry the rejected operation and lets the frontend close the visible `tool_call`.

The lifecycle module is persistence-only and may no-op when no session manager is configured, which keeps unit tests and non-persistent runners from fabricating database state.

The lifecycle module does not emit SSE wire JSON. It may produce typed domain events or event data; `SSEStream` remains the wire boundary.
