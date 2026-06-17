# frontend

Vue 3 + TypeScript chat UI for Alascup Agent.

## Commands

Run from `frontend/vue-project`:

```bash
bun install
bun run dev
bun run test -- --run
bun run type-check
bun run build
bun run test:e2e
```

`frontend/Dockerfile` serves `vue-project/dist/` with nginx. Build `dist/` before building the image.

## Structure

```text
frontend/
  Dockerfile
  nginx.conf
  vue-project/
    src/
      domain/          # wire/domain data shapes
      application/     # state, stream interpretation, session workspace, timeline projection
      infrastructure/  # fetch adapters and markdown rendering
      presentation/    # Vue composables
      components/      # Vue views/widgets
      assets/
    tests/e2e/         # mock-backed Playwright flows
    tests/e2e-live/    # live backend Playwright flows
```

## Runtime Modules

- `ActiveSessionWorkspace` owns active session lifecycle: load list, create draft, select history, delete, adopt `session_init`.
- `ChatStreamInterpreter` owns one chat turn: reasoning/assistant buffers, tool state, approval state, done/error/abort.
- `FetchEventSourceClient` is the SSE transport adapter. It parses wire events into `ChatStreamEvent` and ignores malformed or unknown events.
- `projectTimeline` builds the ordered read model used by `ChatView`.

## SSE Flow

```text
ChatView -> useChat.send(text)
  -> SseClient.connect(...)
  -> FetchEventSourceClient parses wire event
  -> ChatStreamInterpreter.apply(event)
  -> ChatStore / ActiveSessionWorkspace update
  -> ChatView renders projected timeline
```

Supported stream events:

- `session_init`
- `reasoning`
- `thinking_done`
- `assistant`
- `assistant_done`
- `tool_call`
- `tool_result`
- `tool_approval_required`
- `error`
- `done`

## Tests

Unit tests cover behavior, not type-shape assertions. Type structure is checked by `bun run type-check`.

Tracked tests:

- Store behavior
- Stream transport parsing
- Chat stream interpretation
- Active session lifecycle
- Timeline projection
- Markdown rendering
- Key component rendering
- E2E flows under `tests/e2e` and `tests/e2e-live`

Generated outputs such as `test-results/`, coverage, logs, and package-manager files for npm are ignored.
