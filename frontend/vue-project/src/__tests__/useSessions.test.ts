import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('useSessions', () => {
  beforeEach(async () => {
    mockFetch.mockReset()
    // Reset shared module state between tests
    const { useSessions } = await import('@/composables/useSessions')
    const { sessions, activeChatId } = useSessions()
    sessions.value = []
    activeChatId.value = undefined
  })

  it('loadSessions fetches GET /api/sessions and populates list', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { chat_id: 'c1', title: 'Session 1', messages: [], executed_tool_list: [], timestamp: '2026-01-01T00:00:00Z' },
        { chat_id: 'c2', title: 'Session 2', messages: [], executed_tool_list: [], timestamp: '2026-01-02T00:00:00Z' },
      ],
    })

    const { useSessions } = await import('@/composables/useSessions')
    const { sessions, loadSessions } = useSessions()

    expect(sessions.value).toHaveLength(0)
    await loadSessions()
    expect(mockFetch).toHaveBeenCalledWith('/api/sessions')
    expect(sessions.value).toHaveLength(2)
  })

  // FE-010: Session switching
  it('switchSession updates activeChatId', async () => {
    const { useSessions } = await import('@/composables/useSessions')
    const { activeChatId, switchSession } = useSessions()

    expect(activeChatId.value).toBeUndefined()
    await switchSession('c1')
    expect(activeChatId.value).toBe('c1')
    await switchSession('c2')
    expect(activeChatId.value).toBe('c2')
  })

  it('createSession POSTs and adds to list, sets active', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        chat_id: 'new-c1',
        title: undefined,
        messages: [],
        executed_tool_list: [],
        timestamp: '2026-01-01T00:00:00Z',
      }),
    })

    const { useSessions } = await import('@/composables/useSessions')
    const { sessions, activeChatId, createSession } = useSessions()

    const chatId = await createSession()
    expect(mockFetch).toHaveBeenCalledWith('/api/sessions', { method: 'POST' })
    expect(chatId).toBe('new-c1')
    expect(activeChatId.value).toBe('new-c1')
    expect(sessions.value).toHaveLength(1)
  })

  it('sessions are shared across composable instances', async () => {
    const { useSessions: use1 } = await import('@/composables/useSessions')
    const { useSessions: use2 } = await import('@/composables/useSessions')

    const a = use1()
    const b = use2()

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [{ chat_id: 'c1', title: 'S1', messages: [], executed_tool_list: [], timestamp: '2026-01-01T00:00:00Z' }],
    })

    await a.loadSessions()
    expect(b.sessions.value).toHaveLength(1)
    expect(a.sessions.value).toBe(b.sessions.value)
  })

  // FE-018: Loading states
  it('sets isLoadingSessions during loadSessions', async () => {
    let resolveLoad: (value: unknown) => void
    const loadPromise = new Promise((resolve) => { resolveLoad = resolve })
    mockFetch.mockReturnValue(loadPromise)

    const { useSessions } = await import('@/composables/useSessions')
    const { isLoadingSessions, loadSessions } = useSessions()

    expect(isLoadingSessions.value).toBe(false)
    const promise = loadSessions()
    expect(isLoadingSessions.value).toBe(true)

    resolveLoad!({
      ok: true,
      json: async () => [],
    })
    await promise
    expect(isLoadingSessions.value).toBe(false)
  })

  it('sets loadError on non-ok response', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 })
    const { useSessions } = await import('@/composables/useSessions')
    const { loadError, loadSessions } = useSessions()
    await loadSessions()
    expect(loadError.value).toBe('Server error (500)')
  })

  it('sets loadError on network failure', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'))
    const { useSessions } = await import('@/composables/useSessions')
    const { loadError, loadSessions } = useSessions()
    await loadSessions()
    expect(loadError.value).toBe('Cannot connect to server')
  })

  it('clears loadError on successful load', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    })
    const { useSessions } = await import('@/composables/useSessions')
    const { loadError, loadSessions } = useSessions()
    loadError.value = 'old error'
    await loadSessions()
    expect(loadError.value).toBe('')
  })

  it('sets isCreatingSession during createSession', async () => {
    let resolveCreate: (value: unknown) => void
    const createPromise = new Promise((resolve) => { resolveCreate = resolve })
    mockFetch.mockReturnValue(createPromise)

    const { useSessions } = await import('@/composables/useSessions')
    const { isCreatingSession, createSession } = useSessions()

    expect(isCreatingSession.value).toBe(false)
    const promise = createSession()
    expect(isCreatingSession.value).toBe(true)

    resolveCreate!({
      ok: true,
      json: async () => ({ chat_id: 'new-c1', messages: [], executed_tool_list: [], timestamp: '' }),
    })
    await promise
    expect(isCreatingSession.value).toBe(false)
  })
})
