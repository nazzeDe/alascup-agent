import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('useChat message management', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('appends user message on sendMessage', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: done\ndata: {"chat_id":"c1"}\n\n'))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { messages, sendMessage } = useChat()

    expect(messages.value).toHaveLength(0)
    sendMessage('Hello World', 'c1')
    expect(messages.value).toHaveLength(1)
    expect(messages.value[0]!.type).toBe('user')
    expect(messages.value[0]!.content).toBe('Hello World')
  })

  it('appends assistant message on assistant SSE event', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: assistant\ndata: {"chat_id":"c1","message_id":"m1","delta":"Hello"}\n\n' +
          'event: assistant\ndata: {"chat_id":"c1","message_id":"m1","delta":" World"}\n\n' +
          'event: done\ndata: {"chat_id":"c1"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { messages, sendMessage } = useChat()

    sendMessage('Hi', 'c1')
    await vi.waitFor(() => {
      const assistantMsg = messages.value.find(m => m.type === 'assistant')
      expect(assistantMsg).toBeDefined()
      expect(assistantMsg!.content).toBe('Hello World')
    }, { timeout: 1000 })
  })

  it('adds tool call to toolCalls map on tool_call event', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: tool_call\ndata: {"chat_id":"c1","message_id":"tc1","tool_name":"get_cpu","params":{},"is_read_only":true}\n\n' +
          'event: done\ndata: {"chat_id":"c1"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { toolCalls, sendMessage } = useChat()

    sendMessage('check cpu', 'c1')
    await vi.waitFor(() => {
      expect(toolCalls.value.has('tc1')).toBe(true)
      expect(toolCalls.value.get('tc1')!.execution_status).toBe('RUNNING')
    }, { timeout: 1000 })
  })

  it('updates tool call status on tool_result event', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: tool_call\ndata: {"chat_id":"c1","message_id":"tc1","tool_name":"get_cpu","params":{},"is_read_only":true}\n\n' +
          'event: tool_result\ndata: {"chat_id":"c1","message_id":"tc1","tool_name":"get_cpu","execution_status":"SUCCEEDED","output":{"cpu":85}}\n\n' +
          'event: done\ndata: {"chat_id":"c1"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { toolCalls, sendMessage } = useChat()

    sendMessage('check cpu', 'c1')
    await vi.waitFor(() => {
      const tc = toolCalls.value.get('tc1')
      expect(tc?.execution_status).toBe('SUCCEEDED')
      expect(tc?.output).toEqual({ cpu: 85 })
    }, { timeout: 1000 })
  })

  it('sets approval pending on tool_approval_required event', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: tool_approval_required\ndata: {"chat_id":"c1","request_id":"r1","tool_name":"rm","params":{"path":"/tmp"},"reason":"destructive"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { approval_pending, sendMessage } = useChat()

    sendMessage('remove tmp', 'c1')
    await vi.waitFor(() => {
      expect(approval_pending.value).not.toBeNull()
      expect(approval_pending.value!.request_id).toBe('r1')
    }, { timeout: 1000 })
  })

  it('tool_call event adds to toolCalls map but NOT to messages[]', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: tool_call\ndata: {"chat_id":"c1","message_id":"tc1","tool_name":"get_cpu","params":{},"is_read_only":true}\n\n' +
          'event: done\ndata: {"chat_id":"c1"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { messages, toolCalls, sendMessage } = useChat()

    sendMessage('check cpu', 'c1')
    await vi.waitFor(() => {
      expect(toolCalls.value.has('tc1')).toBe(true)
    }, { timeout: 1000 })

    // FE-013: tool_call must NOT be in messages[]
    const toolMessages = messages.value.filter(m => m.type === 'tool_call' || m.type === 'tool_result')
    expect(toolMessages).toHaveLength(0)
  })

  it('submitApproval POSTs to /api/tool-requests/{id}/approval', async () => {
    mockFetch.mockResolvedValue({ ok: true })
    const { useChat } = await import('@/composables/useChat')
    const { submitApproval } = useChat()

    await submitApproval('r1', 'APPROVED')
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/r1/approval',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ approval_status: 'APPROVED' }),
      })
    )
  })

  it('submitApproval keeps modal open on network error', async () => {
    mockFetch.mockRejectedValue(new Error('Network failure'))

    const { useChat } = await import('@/composables/useChat')
    const { approval_pending, submitApproval } = useChat()

    // Simulate approval pending
    approval_pending.value = {
      request_id: 'r1',
      tool_name: 'rm',
      params: {},
      reason: 'destructive',
      chat_id: 'c1',
    }

    try {
      await submitApproval('r1', 'APPROVED')
    } catch {
      // Expected throw from mockRejectedValue
    }
    // FE-015: modal must stay open on error
    expect(approval_pending.value).not.toBeNull()
  })

  it('submitApproval keeps modal open on non-ok response', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 })

    const { useChat } = await import('@/composables/useChat')
    const { approval_pending, submitApproval } = useChat()

    approval_pending.value = {
      request_id: 'r1',
      tool_name: 'rm',
      params: {},
      reason: 'destructive',
      chat_id: 'c1',
    }

    await submitApproval('r1', 'APPROVED')
    // FE-015: modal must stay open on server error
    expect(approval_pending.value).not.toBeNull()
  })

  it('onError does not append system message to messages[] for transient errors', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: error\ndata: {"code":"LLM_CALL_FAILED","message":"LLM unavailable"}\n\n' +
          'event: done\ndata: {"chat_id":"c1"}\n\n'
        ))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { messages, sendMessage } = useChat()

    sendMessage('test', 'c1')
    await vi.waitFor(() => {
      // FE-014: transient errors go to toast, not messages[]
      const systemMessages = messages.value.filter(m => m.type === 'system')
      expect(systemMessages).toHaveLength(0)
    }, { timeout: 1000 })
  })

  it('stops streaming on abort', async () => {
    // Create a stream that never ends
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: assistant\ndata: {"chat_id":"c1","message_id":"m1","delta":"Hello"}\n\n'
        ))
        // Don't close — simulate ongoing stream
      },
    })
    mockFetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', headers: new Headers({ 'content-type': 'text/event-stream' }), body: stream })

    const { useChat } = await import('@/composables/useChat')
    const { is_streaming, sendMessage, abort } = useChat()

    // Start sending but don't await — we want to abort mid-stream
    sendMessage('test', 'c1')

    await vi.waitFor(() => {
      expect(is_streaming.value).toBe(true)
    }, { timeout: 500 })

    abort()

    await vi.waitFor(() => {
      // FE-017: is_streaming should become false after abort
      expect(is_streaming.value).toBe(false)
    }, { timeout: 500 })
  })
})

describe('useChat loadHistory', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('loads session history into messages', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        chat_id: 'c1',
        title: 'Test Session',
        messages: [
          { message_id: 'm1', chat_id: 'c1', timestamp: '2026-01-01T00:00:00Z', type: 'user', content: 'hello' },
          { message_id: 'm2', chat_id: 'c1', timestamp: '2026-01-01T00:00:01Z', type: 'assistant', content: 'hi' },
        ],
        executed_tool_list: [],
        timestamp: '2026-01-01T00:00:01Z',
      }),
    })

    const { useChat } = await import('@/composables/useChat')
    const { messages, loadHistory } = useChat()

    await loadHistory('c1')
    expect(messages.value).toHaveLength(2)
  })
})
