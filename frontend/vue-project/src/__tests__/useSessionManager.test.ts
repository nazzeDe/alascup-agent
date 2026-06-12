import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

// Reset module-level state by re-importing
async function freshManager(overrides?: Record<string, unknown>) {
  const mod = await import('@/composables/useSessionManager')
  return mod.useSessionManager(overrides)
}

describe('useSessionManager', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // --- Tracer bullet 1: createDraft ---

  it('createDraft sets activeChatId to null and sends POST /api/sessions', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ chat_id: 'draft-123' }) })

    const manager = await freshManager()
    mockFetch.mockClear()

    manager.createDraft()

    expect(manager.activeChatId.value).toBeNull()
    expect(mockFetch).toHaveBeenCalledWith('/api/sessions', { method: 'POST' })
  })

  it('createDraft updates activeChatId when backend returns chat_id', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ chat_id: 'new-session-x' }) })

    const manager = await freshManager()
    manager.createDraft()

    // Kick microtask queue so .then() runs
    await vi.waitFor(() => {
      expect(manager.activeChatId.value).toBe('new-session-x')
    })
    expect(manager.sessions.value).toHaveLength(1)
    expect(manager.sessions.value[0]!.chat_id).toBe('new-session-x')
  })

  it('createDraft keeps null on fetch error (graceful fallback)', async () => {
    mockFetch.mockRejectedValue(new Error('network down'))

    const manager = await freshManager()
    manager.createDraft()

    expect(manager.activeChatId.value).toBeNull()
    // No crash — works
  })

  it('get(null) returns a draft SessionState after createDraft', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ chat_id: 'draft-1' }) })

    const manager = await freshManager()
    manager.createDraft()

    const state = manager.get(null)

    expect(state).toBeDefined()
    expect(state.messages.value).toEqual([])
    expect(state.isStreaming.value).toBe(false)
    expect(state.draftInput.value).toBe('')
  })

  it('get returns the same SessionState for the same chatId', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ chat_id: 'draft-2' }) })

    const manager = await freshManager()
    manager.createDraft()

    const a = manager.get(null)
    const b = manager.get(null)

    expect(a).toBe(b)
  })

  // --- Tracer bullet 2: sendMessage creates session ---

  it('sendMessage in draft sets chatId from done event and updates session list', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null
    let capturedOnSessionId: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown, _signal: unknown, onSessionId: unknown) => {
      capturedCallbacks = callbacks
      capturedOnSessionId = onSessionId
      return Promise.resolve()
    })

    const manager = await freshManager({ _connect: mockConnect })
    // createDraft will call fetch — mock it
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ chat_id: 'draft-pre' }) })

    manager.createDraft()
    const state = manager.get(null)
    state.sendMessage('hello')

    // user message added immediately (optimistic)
    expect(state.messages.value).toHaveLength(1)
    expect(state.messages.value[0]!.type).toBe('user')
    expect(state.messages.value[0]!.content).toBe('hello')
    expect(state.isStreaming.value).toBe(true)

    // Simulate onopen delivering X-Session-ID
    capturedOnSessionId('new-session-1')

    // Simulate server events
    capturedCallbacks.on_session_init({ chat_id: 'new-session-1' })
    capturedCallbacks.on_assistant({ delta: 'Hi there!' })
    capturedCallbacks.on_assistant_done({})
    capturedCallbacks.on_done({})

    // After done, chat_id should be assigned
    expect(manager.activeChatId.value).toBe('new-session-1')
    // session list updated
    expect(manager.sessions.value).toHaveLength(1)
    expect(manager.sessions.value[0]!.chat_id).toBe('new-session-1')
    // draft (null key) state moved to real key
    const movedState = manager.get('new-session-1')
    expect(movedState.messages.value).toHaveLength(2) // user + assistant
  })

  // --- Tracer bullet 3: session isolation ---

  it('each session has independent messages and draft', async () => {
    const manager = await freshManager()
    manager.activeChatId.value = 'a'

    const stateA = manager.get('a')
    stateA.sendMessage('msg in A')
    stateA.draftInput.value = 'draft A'

    // Switch to B
    manager.activeChatId.value = 'b'
    const stateB = manager.get('b')
    stateB.sendMessage('msg in B')
    stateB.draftInput.value = 'draft B'

    // Switch back to A — state preserved
    expect(stateA.messages.value[0]!.content).toBe('msg in A')
    expect(stateA.draftInput.value).toBe('draft A')

    // B still has its own state
    expect(stateB.messages.value[0]!.content).toBe('msg in B')
    expect(stateB.draftInput.value).toBe('draft B')
  })

  it('switching session does not abort background streaming', async () => {
    const mockConnect = vi.fn()
    let capturedSignalA: AbortSignal | null = null

    mockConnect.mockImplementation((_body: unknown, _callbacks: unknown, signal: AbortSignal) => {
      capturedSignalA = signal
      return new Promise(() => {}) // never resolves — streaming in progress
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 'a'

    const stateA = manager.get('a')
    stateA.sendMessage('hello')
    expect(stateA.isStreaming.value).toBe(true)

    // Switch to B
    manager.activeChatId.value = 'b'

    // A's streaming should still be active
    expect(stateA.isStreaming.value).toBe(true)
    expect(capturedSignalA!.aborted).toBe(false)
  })

  // --- Tracer bullet 4: approval as timeline event ---

  it('tool_approval_required SSE event sets approvalEvent on the session', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')

    state.sendMessage('do something dangerous')

    capturedCallbacks.on_tool_call({
      call_id: 'tc-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      is_read_only: false,
      server: 'filesystem',
    })

    capturedCallbacks.on_tool_approval_required({
      chat_id: 's1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs to delete system file',
      call_id: 'tc-1',
    })

    expect(state.approvalEvent.value).not.toBeNull()
    expect(state.approvalEvent.value!.tool_name).toBe('rm')
    expect(state.approvalEvent.value!.status).toBe('pending')
  })

  it('approve sets status to approved and leaves event in place', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) })

    const manager = await freshManager()
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')

    // Manually set approval event
    state.approvalEvent.value = {
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs to delete system file',
      status: 'pending',
      message: '',
    }

    await state.approve('req-1', 'looks fine')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-1/approval',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approval_status: 'APPROVED', reason: 'looks fine' }),
      })
    )
    expect(state.approvalEvent.value!.status).toBe('approved')
  })

  it('reject sets status to rejected with message', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) })

    const manager = await freshManager()
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')

    state.approvalEvent.value = {
      request_id: 'req-2',
      tool_name: 'write_file',
      params: {},
      reason: 'would overwrite config',
      status: 'pending',
      message: '',
    }

    await state.reject('req-2', 'not safe')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-2/approval',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ approval_status: 'REJECTED', reason: 'not safe' }),
      })
    )
    expect(state.approvalEvent.value!.status).toBe('rejected')
    expect(state.approvalEvent.value!.message).toBe('not safe')
  })

  // --- Tracer bullet 5: deleteSession ---

  it('deleteSession aborts streaming, removes state and from list', async () => {
    const mockConnect = vi.fn()
    let capturedSignal: AbortSignal | null = null

    mockConnect.mockImplementation((_body: unknown, _callbacks: unknown, signal: AbortSignal) => {
      capturedSignal = signal
      return new Promise(() => {}) // never resolves — streaming in progress
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'

    // Setup session in list
    manager.sessions.value = [
      { chat_id: 's1', messages: [], executed_tool_list: [], timestamp: '' },
      { chat_id: 's2', messages: [], executed_tool_list: [], timestamp: '' },
    ]

    const state = manager.get('s1')
    state.sendMessage('hello')
    expect(state.isStreaming.value).toBe(true)

    // Mock DELETE
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) })

    await manager.deleteSession('s1')

    // SSE should be aborted
    expect(capturedSignal!.aborted).toBe(true)
    // state removed from instances
    expect(manager.get('s1')).not.toBe(state) // new one created lazily
    // session removed from list
    expect(manager.sessions.value).toHaveLength(1)
    expect(manager.sessions.value[0]!.chat_id).toBe('s2')
  })

  it('deleteSession clears activeChatId if deleting active session', async () => {
    const manager = await freshManager()
    manager.activeChatId.value = 's1'
    manager.sessions.value = [
      { chat_id: 's1', messages: [], executed_tool_list: [], timestamp: '' },
    ]

    mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    await manager.deleteSession('s1')

    expect(manager.activeChatId.value).toBeNull()
    expect(manager.sessions.value).toHaveLength(0)
  })

  // --- Bug 7: loadHistory null check ---

  it('loadHistory with null chatId clears state without fetching', async () => {
    mockFetch.mockReset()

    const manager = await freshManager()
    const state = manager.get(null)
    // Pre-populate state
    state.messages.value = [{ message_id: 'm1', chat_id: '', timestamp: '', type: 'user', content: 'old' }]
    state.toolCalls.value = new Map([['t1', { call_id: 't1', chat_id: '', tool_name: 'x', is_read_only: true, execution_status: 'SUCCEEDED', timestamp: '' }]])

    const ok = await state.loadHistory()

    expect(ok).toBe(true)
    expect(state.messages.value).toEqual([])
    expect(state.toolCalls.value.size).toBe(0)
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('loadHistory with valid chatId fetches from API', async () => {
    mockFetch.mockReset()
    const sessionData = {
      chat_id: 'abc-123',
      title: 'Test',
      messages: [{ message_id: 'm2', chat_id: 'abc-123', timestamp: '', type: 'user', content: 'hi' }],
      executed_tool_list: [],
      timestamp: '',
    }
    mockFetch.mockResolvedValue({ ok: true, json: async () => sessionData })

    const manager = await freshManager()
    const state = manager.get('abc-123')
    state.messages.value = [{ message_id: 'old', chat_id: 'abc-123', timestamp: '', type: 'user', content: 'old' }]

    const ok = await state.loadHistory()

    expect(ok).toBe(true)
    expect(mockFetch).toHaveBeenCalledWith('/api/sessions/abc-123')
  })

  // --- Error/edge-case handling ---

  it('on_error resets agentPhase to idle and sets isStreaming to false', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    expect(state.isStreaming.value).toBe(true)
    expect(state.agentPhase.value).toBe('thinking')

    // Simulate SSE error event
    capturedCallbacks.on_error({ code: 'AGENT_CRASH', message: 'boom' })

    expect(state.isStreaming.value).toBe(false)
    expect(state.agentPhase.value).toBe('idle')
    expect(state.connectionError.value).toEqual({ code: 'AGENT_CRASH', message: 'boom' })
  })

  it('on_done preserves connectionError set by preceding on_error', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    // Simulate error then done (SSE stream always emits done in finally)
    capturedCallbacks.on_error({ code: 400, message: 'messages[1]: missing field type' })
    capturedCallbacks.on_done({})

    expect(state.isStreaming.value).toBe(false)
    expect(state.agentPhase.value).toBe('done')
    // connectionError MUST survive the on_done that follows on_error
    expect(state.connectionError.value).toEqual({ code: 400, message: 'messages[1]: missing field type' })
  })

  it('on_done clears connectionError when no preceding error', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    capturedCallbacks.on_session_init({ chat_id: 's1' })
    capturedCallbacks.on_assistant({ delta: 'OK' })
    capturedCallbacks.on_done({})

    expect(state.isStreaming.value).toBe(false)
    expect(state.agentPhase.value).toBe('done')
    expect(state.connectionError.value).toBeNull()
  })

  it('connectFn resolving cleanly (no done event) cleans up streaming state', async () => {
    const mockConnect = vi.fn()

    // Promise resolves — simulates clean SSE close without done event
    mockConnect.mockResolvedValue(undefined)

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    expect(state.isStreaming.value).toBe(true)
    expect(state.agentPhase.value).toBe('thinking')

    // Wait for the promise resolution to propagate
    await vi.waitFor(() => {
      expect(state.isStreaming.value).toBe(false)
    })
    expect(state.agentPhase.value).toBe('idle')
  })

  it('connectFn rejecting (AbortError) leaves agentPhase as is (abort already handled)', async () => {
    const mockConnect = vi.fn()

    mockConnect.mockRejectedValue(new DOMException('aborted', 'AbortError'))

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    await vi.waitFor(() => {
      expect(state.isStreaming.value).toBe(false)
    })
    // AbortError should not set connectionError
    expect(state.connectionError.value).toBeNull()
  })

  it('on_thinking_done transitions phase from thinking to responding', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    expect(state.agentPhase.value).toBe('thinking')

    // Simulate reasoning stream
    capturedCallbacks.on_reasoning({ delta: 'thinking...' })
    // thinking_done marks end of reasoning
    capturedCallbacks.on_thinking_done({})

    // Phase should transition to responding after thinking completes
    expect(state.agentPhase.value).toBe('responding')
    expect(state.reasonings.value).toHaveLength(1)
    expect(state.reasonings.value[0]!.done).toBe(true)
  })

  // --- New wire contract: call_id in tool events ---

  it('tool_call uses call_id as map key', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('run tool')

    capturedCallbacks.on_tool_call({
      call_id: 'call-abc',
      tool_name: 'read_file',
      params: { path: '/tmp/test' },
      is_read_only: true,
      server: 'filesystem',
    })

    const tc = state.toolCalls.value.get('call-abc')
    expect(tc).toBeDefined()
    expect(tc!.call_id).toBe('call-abc')
    expect(tc!.tool_name).toBe('read_file')
    expect(tc!.execution_status).toBe('RUNNING')
  })

  it('tool_result updates via call_id', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('run tool')

    capturedCallbacks.on_tool_call({
      call_id: 'call-xyz',
      tool_name: 'write_file',
      params: {},
      is_read_only: false,
    })

    capturedCallbacks.on_tool_result({
      call_id: 'call-xyz',
      execution_status: 'SUCCEEDED',
      output: { bytes_written: 42 },
      execution_time_ms: 150,
    })

    const tc = state.toolCalls.value.get('call-xyz')
    expect(tc!.execution_status).toBe('SUCCEEDED')
    expect(tc!.output).toEqual({ bytes_written: 42 })
    expect(tc!.execution_time_ms).toBe(150)
  })

  // --- New wire contract: assistant_done as message boundary ---

  it('assistant_done creates a message from accumulated buffer', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('hello')

    capturedCallbacks.on_session_init({ chat_id: 's1' })
    capturedCallbacks.on_assistant({ delta: 'Hi ' })
    capturedCallbacks.on_assistant({ delta: 'there!' })

    // No message yet — delta accumulated but not committed
    expect(state.messages.value).toHaveLength(1) // only user message
    expect(state.agentPhase.value).toBe('responding')

    capturedCallbacks.on_assistant_done({})

    expect(state.messages.value).toHaveLength(2) // user + assistant
    expect(state.messages.value[1]!.type).toBe('assistant')
    expect(state.messages.value[1]!.content).toBe('Hi there!')
    expect(state.messages.value[1]!.message_id).toBeTruthy()
    expect(state.messages.value[1]!.chat_id).toBe('s1')
  })

  it('assistant handles delta-only events (no chat_id, no message_id)', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    // Simulate delta-only assistant event (new wire format)
    capturedCallbacks.on_assistant({ delta: 'Hello' })
    capturedCallbacks.on_assistant({ delta: ' World' })

    // Buffer accumulated but no message committed until assistant_done
    expect(state.messages.value).toHaveLength(1) // only user

    capturedCallbacks.on_assistant_done({})
    expect(state.messages.value).toHaveLength(2)
    expect(state.messages.value[1]!.content).toBe('Hello World')
  })

  it('thinking_done marks reasoning entry done', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('think')

    capturedCallbacks.on_reasoning({ delta: 'hmm' })
    expect(state.reasonings.value).toHaveLength(1)
    expect(state.reasonings.value[0]!.done).toBe(false)

    capturedCallbacks.on_thinking_done({})

    expect(state.reasonings.value[0]!.done).toBe(true)
    expect(state.agentPhase.value).toBe('responding')
  })

  // --- Bug 3: on_done force-fail tools that never received tool_result ---

  it('on_done does not force-fail tools that already received tool_result', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    // Fire tool_call, then tool_result (SUCCEEDED), then done
    capturedCallbacks.on_tool_call({
      call_id: 'tc-1',
      tool_name: 'read_file',
      params: { path: '/tmp/test' },
      is_read_only: true,
      server: 'filesystem',
    })

    capturedCallbacks.on_tool_result({
      call_id: 'tc-1',
      execution_status: 'SUCCEEDED',
      output: { content: 'hello' },
      execution_time_ms: 100,
    })

    capturedCallbacks.on_done({})

    const tc = state.toolCalls.value.get('tc-1')
    expect(tc!.execution_status).toBe('SUCCEEDED')
    expect(tc!.error).toBeUndefined()
    expect(state.agentPhase.value).toBe('done')
  })

  it('on_done force-fails tools that never received tool_result', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    // Fire tool_call then done (skip tool_result)
    capturedCallbacks.on_tool_call({
      call_id: 'tc-1',
      tool_name: 'write_file',
      params: { path: '/tmp/out' },
      is_read_only: false,
      server: 'filesystem',
    })

    capturedCallbacks.on_done({})

    const tc = state.toolCalls.value.get('tc-1')
    expect(tc!.execution_status).toBe('FAILED')
    expect(tc!.error).toEqual({ message: 'Connection closed before tool completed' })
  })

  // ── Bug 1: on_tool_approval_required transitions RUNNING → PENDING_APPROVAL ──

  it('on_tool_approval_required sets tool execution_status to PENDING_APPROVAL', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('do something dangerous')

    // tool_call first — sets RUNNING
    capturedCallbacks.on_tool_call({
      call_id: 'tc-pending',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      is_read_only: false,
      server: 'filesystem',
    })

    expect(state.toolCalls.value.get('tc-pending')!.execution_status).toBe('RUNNING')

    // approval_required — should transition to PENDING_APPROVAL
    capturedCallbacks.on_tool_approval_required({
      chat_id: 's1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs approval',
      call_id: 'tc-pending',
    })

    const tc = state.toolCalls.value.get('tc-pending')
    expect(tc!.execution_status).toBe('PENDING_APPROVAL')
  })

  it('on_done does NOT force-fail tools with PENDING_APPROVAL status', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('test')

    // tool_call
    capturedCallbacks.on_tool_call({
      call_id: 'tc-waiting',
      tool_name: 'write_file',
      params: { path: '/tmp/out' },
      is_read_only: false,
      server: 'filesystem',
    })

    // approval_required — sets PENDING_APPROVAL
    capturedCallbacks.on_tool_approval_required({
      chat_id: 's1',
      request_id: 'req-1',
      tool_name: 'write_file',
      params: { path: '/tmp/out' },
      reason: 'needs approval',
      call_id: 'tc-waiting',
    })

    // done — should NOT force-fail the PENDING_APPROVAL tool
    capturedCallbacks.on_done({})

    const tc = state.toolCalls.value.get('tc-waiting')
    expect(tc!.execution_status).toBe('PENDING_APPROVAL')
    expect(tc!.error).toBeUndefined()
  })

  // ── Full approval flow: tool_call → approval_required → tool_result → done ──

  it('approval flow: tool survives done when approval followed by tool_result', async () => {
    const mockConnect = vi.fn()
    let capturedCallbacks: any = null

    mockConnect.mockImplementation((_body: unknown, callbacks: unknown) => {
      capturedCallbacks = callbacks
      return new Promise(() => {})
    })

    const manager = await freshManager({ _connect: mockConnect })
    manager.activeChatId.value = 's1'
    const state = manager.get('s1')
    state.sendMessage('run bash')

    // 1. tool_call — RUNNING
    capturedCallbacks.on_tool_call({
      call_id: 'tc-full',
      tool_name: 'bash',
      params: { command: 'ls' },
      is_read_only: false,
      server: 'tool-server',
    })

    // 2. approval_required — PENDING_APPROVAL
    capturedCallbacks.on_tool_approval_required({
      chat_id: 's1',
      request_id: 'req-full',
      tool_name: 'bash',
      params: { command: 'ls' },
      reason: 'needs approval',
      call_id: 'tc-full',
    })

    expect(state.toolCalls.value.get('tc-full')!.execution_status).toBe('PENDING_APPROVAL')

    // 3. tool_result arrives (after approval) — SUCCEEDED
    capturedCallbacks.on_tool_result({
      call_id: 'tc-full',
      execution_status: 'SUCCEEDED',
      output: { stdout: 'file1\nfile2' },
      execution_time_ms: 100,
    })

    // 4. done — should NOT force-fail
    capturedCallbacks.on_done({})

    const tc = state.toolCalls.value.get('tc-full')
    expect(tc!.execution_status).toBe('SUCCEEDED')
    expect(tc!.error).toBeUndefined()
    expect(tc!.output).toEqual({ stdout: 'file1\nfile2' })
  })

  // --- Bug 7: loadHistory null check ---
  it('loadHistory treats empty string chatId as real session, not null', async () => {
    // Bug: !chatId.value treats "" as falsy, same as null.
    // The original === null check only matched null, letting "" through as a valid id.
    mockFetch.mockReset()
    mockFetch.mockResolvedValue({ ok: false, status: 404 })

    const manager = await freshManager()
    // Simulate a session created with empty string chat_id (rare but possible)
    const state = manager.get('')
    state.messages.value = [{ message_id: 'm1', chat_id: '', timestamp: '', type: 'user', content: 'x' }]

    const ok = await state.loadHistory()

    // Should attempt to fetch (empty string is not null), not short-circuit
    expect(mockFetch).toHaveBeenCalled()
    expect(ok).toBe(false) // fetch fails with 404
  })

})
