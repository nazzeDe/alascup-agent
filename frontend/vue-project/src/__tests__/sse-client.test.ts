import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the fetchEventSource module before importing the SseClient
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(),
}))

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { FetchEventSourceClient } from '@/infrastructure/sse-client'

const mockFetchEventSource = fetchEventSource as ReturnType<typeof vi.fn>

describe('infrastructure/sse-client — FetchEventSourceClient', () => {
  let onEvent: ReturnType<typeof vi.fn>
  let client: FetchEventSourceClient

  beforeEach(() => {
    mockFetchEventSource.mockReset()
    client = new FetchEventSourceClient()
    onEvent = vi.fn()
  })

  it('connect calls fetchEventSource with correct URL and options', async () => {
    mockFetchEventSource.mockResolvedValue(undefined)

    const ctrl = new AbortController()
    await client.connect(
      { chat_id: 'c1', message: 'hello' },
      onEvent,
      ctrl.signal,
    )

    expect(mockFetchEventSource).toHaveBeenCalledTimes(1)
    const callArgs = mockFetchEventSource.mock.calls[0]
    expect(callArgs[0]).toBe('/api/chat')

    const options = callArgs[1]
    expect(options.method).toBe('POST')
    expect(options.headers).toEqual({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    })
    expect(JSON.parse(options.body)).toEqual({
      chat_id: 'c1',
      message: 'hello',
    })
    expect(options.signal).toBe(ctrl.signal)
    expect(options.openWhenHidden).toBe(true)
  })

  it('does not install a response-header session handler', async () => {
    mockFetchEventSource.mockResolvedValue(undefined)

    const ctrl = new AbortController()
    await client.connect({ message: 'hi' }, onEvent, ctrl.signal)

    const options = mockFetchEventSource.mock.calls[0]![1]
    expect(options.onopen).toBeUndefined()
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('dispatches onmessage events to correct callbacks', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        // Simulate various SSE events
        options.onmessage({ event: 'session_init', data: JSON.stringify({ chat_id: 'c1' }) })
        options.onmessage({ event: 'assistant', data: JSON.stringify({ delta: 'Hello' }) })
        options.onmessage({ event: 'assistant_done', data: JSON.stringify({}) })
        options.onmessage({ event: 'done', data: JSON.stringify({}) })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledWith({ type: 'session_init', data: { chat_id: 'c1' } })
    expect(onEvent).toHaveBeenCalledWith({ type: 'assistant', data: { delta: 'Hello' } })
    expect(onEvent).toHaveBeenCalledWith({ type: 'assistant_done', data: {} })
    expect(onEvent).toHaveBeenCalledWith({ type: 'done', data: {} })
  })

  it('dispatches tool_call event', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({
          event: 'tool_call',
          data: JSON.stringify({
            call_id: 'tc-1',
            tool_name: 'read_file',
            params: { path: '/tmp/test' },
            is_read_only: true,
            server: 'filesystem',
          }),
        })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledWith({
      type: 'tool_call',
      data: {
      call_id: 'tc-1',
      tool_name: 'read_file',
      params: { path: '/tmp/test' },
      is_read_only: true,
      server: 'filesystem',
      },
    })
  })

  it('dispatches tool_result event', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({
          event: 'tool_result',
          data: JSON.stringify({
            call_id: 'tc-1',
            execution_status: 'SUCCEEDED',
            output: { bytes_written: 42 },
            execution_time_ms: 150,
          }),
        })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledWith({
      type: 'tool_result',
      data: {
      call_id: 'tc-1',
      execution_status: 'SUCCEEDED',
      output: { bytes_written: 42 },
      execution_time_ms: 150,
      },
    })
  })

  it('dispatches tool_approval_required event', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({
          event: 'tool_approval_required',
          data: JSON.stringify({
            chat_id: 'c1',
            request_id: 'req-1',
            tool_name: 'rm',
            params: { path: '/etc/hosts' },
            reason: 'needs approval',
            call_id: 'tc-1',
          }),
        })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledWith({
      type: 'tool_approval_required',
      data: {
      chat_id: 'c1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs approval',
      call_id: 'tc-1',
      },
    })
  })

  it('dispatches error event', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({
          event: 'error',
          data: JSON.stringify({ code: 'AGENT_CRASH', message: 'boom' }),
        })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledWith({
      type: 'error',
      data: {
      code: 'AGENT_CRASH',
      message: 'boom',
      },
    })
  })

  it('skips malformed JSON gracefully', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({ event: 'assistant', data: 'not-json' })
        options.onmessage({ event: 'assistant', data: JSON.stringify({ delta: 'ok' }) })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    // Malformed event skipped, valid event processed
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledWith({ type: 'assistant', data: { delta: 'ok' } })
  })

  it('skips messages with missing event or data', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({ event: '', data: '{}' })
        options.onmessage({ event: 'done', data: '' })
        options.onmessage({ event: 'done', data: '{}' })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    await client.connect({}, onEvent, ctrl.signal)

    // Only the valid event should trigger
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledWith({ type: 'done', data: {} })
  })

  it('onerror dispatches NETWORK_ERROR event and rejects', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        // fetchEventSource's onerror throws to abort the stream; the library
        // catches it and returns a rejected promise. Simulate that behavior.
        try {
          options.onerror(new Error('Connection lost'))
        } catch {
          // Expected — onerror throws to signal abort
        }
        return Promise.reject(new Error('Connection lost'))
      },
    )

    const ctrl = new AbortController()
    await expect(
      client.connect({}, onEvent, ctrl.signal),
    ).rejects.toThrow('Connection lost')

    expect(onEvent).toHaveBeenCalledWith({
      type: 'error',
      data: {
        code: 'NETWORK_ERROR',
        message: 'Error: Connection lost',
      },
    })
  })

  it('skips unknown stream event types', async () => {
    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onmessage({ event: 'future_event', data: JSON.stringify({ ok: true }) })
        options.onmessage({ event: 'done', data: JSON.stringify({}) })
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()

    await client.connect({}, onEvent, ctrl.signal)

    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledWith({ type: 'done', data: {} })
  })
})
