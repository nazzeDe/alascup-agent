import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock global fetch
const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

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
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers({ 'content-type': 'text/event-stream' }),
      body: stream,
    })

    const { useSSE } = await import('@/composables/useSSE')
    const { connect } = useSSE()

    const doneSpy = vi.fn()
    await connect({ message: 'test message' }, { on_done: doneSpy })

    expect(mockFetch).toHaveBeenCalledWith('/api/chat-turn', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
    }))
    const bodyArg = JSON.parse(mockFetch.mock.calls[0]![1]!.body as string)
    expect(bodyArg.message).toBe('test message')
    expect(doneSpy).toHaveBeenCalled()
  })

  it('calls on_error when server returns non-200', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      headers: new Headers({ 'content-type': 'text/plain' }),
    })
    const { useSSE } = await import('@/composables/useSSE')
    const { connect } = useSSE()
    const errorSpy = vi.fn()
    await connect({ message: 'test' }, { on_error: errorSpy })
    expect(errorSpy).toHaveBeenCalled()
  })

  it('sets isStreaming to false after completion', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: done\ndata: {"chat_id":"c1"}\n\n'))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers({ 'content-type': 'text/event-stream' }),
      body: stream,
    })
    const { useSSE } = await import('@/composables/useSSE')
    const { connect, isStreaming } = useSSE()
    expect(isStreaming.value).toBe(false)
    await connect({ message: 'test' }, {})
    expect(isStreaming.value).toBe(false)
  })
})
