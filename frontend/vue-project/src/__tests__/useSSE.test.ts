import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock global fetch
const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

// Minimal SSE parser with cross-chunk line buffering (mirrors useSSE streaming logic)
function parseSSEStream(chunks: string[]): Array<{ event: string; data: unknown }> {
  const results: Array<{ event: string; data: unknown }> = []
  let currentEvent = ''
  let currentData = ''
  let buffer = ''

  for (const chunk of chunks) {
    buffer += chunk
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6)
      } else if (line === '' && currentEvent && currentData) {
        try {
          results.push({ event: currentEvent, data: JSON.parse(currentData) })
        } catch { /* skip */ }
        currentEvent = ''
        currentData = ''
      }
    }
  }
  return results
}

describe('SSE Parser', () => {
  // FE-001: SSE assistant event parse
  it('parses assistant event with delta text', () => {
    const input = 'event: assistant\ndata: {"chat_id":"c1","message_id":"m1","delta":"current CPU"}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    expect(results[0]!.event).toBe('assistant')
    expect(results[0]!.data).toEqual({ chat_id: 'c1', message_id: 'm1', delta: 'current CPU' })
  })

  // FE-002: SSE tool_call event parse
  it('parses tool_call event with tool_name and is_read_only', () => {
    const input = 'event: tool_call\ndata: {"chat_id":"c1","message_id":"m2","tool_name":"get_cpu_info","params":{},"is_read_only":true}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    expect(results[0]!.event).toBe('tool_call')
    const data = results[0]!.data as Record<string, unknown>
    expect(data.tool_name).toBe('get_cpu_info')
    expect(data.is_read_only).toBe(true)
  })

  // FE-003: SSE tool_result event parse
  it('parses tool_result event with SUCCEEDED status', () => {
    const input = 'event: tool_result\ndata: {"chat_id":"c1","message_id":"m2","tool_name":"get_cpu_info","execution_status":"SUCCEEDED","output":{"cpu":85}}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    const data = results[0]!.data as Record<string, unknown>
    expect(data.execution_status).toBe('SUCCEEDED')
  })

  // FE-004: SSE tool_approval_required event
  it('parses tool_approval_required event with request_id', () => {
    const input = 'event: tool_approval_required\ndata: {"chat_id":"c1","request_id":"r1","tool_name":"delete_temp_files","params":{"path":"/tmp"},"reason":"High risk"}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    const data = results[0]!.data as Record<string, unknown>
    expect(data.request_id).toBe('r1')
  })

  // FE-005: SSE error event
  it('parses error event with code and message', () => {
    const input = 'event: error\ndata: {"code":"LLM_CALL_FAILED","message":"LLM unavailable"}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    expect(results[0]!.event).toBe('error')
  })

  // FE-006: SSE done event
  it('parses done event and signals completion', () => {
    const input = 'event: done\ndata: {"chat_id":"c1"}\n\n'
    const results = parseSSEStream([input])
    expect(results).toHaveLength(1)
    expect(results[0]!.event).toBe('done')
  })

  it('parses multiple events in a single stream', () => {
    const input = [
      'event: assistant\ndata: {"chat_id":"c1","message_id":"m1","delta":"Hello"}\n\n',
      'event: tool_call\ndata: {"chat_id":"c1","message_id":"m2","tool_name":"get_cpu","params":{},"is_read_only":true}\n\n',
      'event: done\ndata: {"chat_id":"c1"}\n\n',
    ]
    const results = parseSSEStream(input)
    expect(results).toHaveLength(3)
  })

  // FE-011: Buffer handles partial reads — event frame split across chunks at line boundary
  it('buffers incomplete SSE frames across read chunks', () => {
    // First chunk: event line only. Second chunk: data line + terminating newline.
    // The parser must carry the incomplete event over to the next chunk.
    const results = parseSSEStream([
      'event: assistant' + '\n' + 'data: {"chat_id":"c1","message_id":"m1","delta":"Hi"}',
      '\n\n',
    ])
    expect(results).toHaveLength(1)
    expect(results[0]!.event).toBe('assistant')

    // Second event: tool_call frame split so 'data:' arrives in a separate chunk from 'event:'
    const results2 = parseSSEStream([
      'event: tool_call\n',
      'data: {"tool_name":"get_cpu","is_read_only":true}\n\n',
    ])
    expect(results2).toHaveLength(1)
    expect(results2[0]!.event).toBe('tool_call')
  })
})

describe('useSSE connect', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('sends POST to /api/chat-turn with message', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: done\ndata: {"chat_id":"c1"}\n\n'))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, body: stream })

    const { useSSE } = await import('@/composables/useSSE')
    const { connect } = useSSE()

    const doneSpy = vi.fn()
    await connect({ message: 'test message' }, { onDone: doneSpy })

    expect(mockFetch).toHaveBeenCalledWith('/api/chat-turn', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
    }))
    const bodyArg = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string)
    expect(bodyArg.message).toBe('test message')
    expect(doneSpy).toHaveBeenCalled()
  })

  it('calls onError when server returns non-200', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 })
    const { useSSE } = await import('@/composables/useSSE')
    const { connect } = useSSE()
    const errorSpy = vi.fn()
    await connect({ message: 'test' }, { onError: errorSpy })
    expect(errorSpy).toHaveBeenCalledWith(expect.objectContaining({ code: 'HTTP_ERROR' }))
  })

  it('sets isStreaming to false after completion', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: done\ndata: {"chat_id":"c1"}\n\n'))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({ ok: true, body: stream })
    const { useSSE } = await import('@/composables/useSSE')
    const { connect, isStreaming } = useSSE()
    expect(isStreaming.value).toBe(false)
    await connect({ message: 'test' }, {})
    expect(isStreaming.value).toBe(false)
  })
})
