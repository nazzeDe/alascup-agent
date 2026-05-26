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

  it('createDraft sets activeChatId to null without HTTP request', async () => {
    const manager = await freshManager()
    mockFetch.mockClear()

    manager.createDraft()

    expect(manager.activeChatId.value).toBeNull()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('get(null) returns a draft SessionState after createDraft', async () => {
    const manager = await freshManager()
    manager.createDraft()

    const state = manager.get(null)

    expect(state).toBeDefined()
    expect(state.messages.value).toEqual([])
    expect(state.isStreaming.value).toBe(false)
    expect(state.draftInput.value).toBe('')
  })

  it('get returns the same SessionState for the same chatId', async () => {
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
    mockFetch.mockClear()

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
    capturedCallbacks.on_assistant({ chat_id: '', message_id: 'm1', delta: 'Hi there!' })
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

    capturedCallbacks.on_tool_approval_required({
      chat_id: 's1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs to delete system file',
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

    manager.deleteSession('s1')

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

    manager.deleteSession('s1')

    expect(manager.activeChatId.value).toBeNull()
    expect(manager.sessions.value).toHaveLength(0)
  })

})
