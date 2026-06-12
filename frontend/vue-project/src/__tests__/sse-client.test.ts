import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { SSECallbacks } from '@/domain/sse-events'

// Mock the fetchEventSource module before importing the SseClient
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(),
}))

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { FetchEventSourceClient } from '@/infrastructure/sse-client'

const mockFetchEventSource = fetchEventSource as ReturnType<typeof vi.fn>

describe('infrastructure/sse-client — FetchEventSourceClient', () => {
  let callbacks: SSECallbacks
  let client: FetchEventSourceClient

  beforeEach(() => {
    mockFetchEventSource.mockReset()
    client = new FetchEventSourceClient()
    callbacks = {
      on_assistant: vi.fn(),
      on_assistant_done: vi.fn(),
      on_reasoning: vi.fn(),
      on_thinking_done: vi.fn(),
      on_tool_call: vi.fn(),
      on_tool_result: vi.fn(),
      on_tool_approval_required: vi.fn(),
      on_session_init: vi.fn(),
      on_error: vi.fn(),
      on_done: vi.fn(),
    }
  })

  it('connect calls fetchEventSource with correct URL and options', async () => {
    mockFetchEventSource.mockResolvedValue(undefined)

    const ctrl = new AbortController()
    await client.connect(
      { chat_id: 'c1', message: 'hello' },
      callbacks,
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

  it('calls onSessionId when onopen delivers X-Session-ID header', async () => {
    const fakeResponse = {
      headers: {
        get: vi.fn((name: string) => {
          if (name === 'X-Session-ID') return 'session-abc'
          return null
        }),
      },
    }

    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        // Immediately invoke onopen
        options.onopen(fakeResponse)
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    let capturedSessionId = ''

    await client.connect(
      { message: 'hi' },
      callbacks,
      ctrl.signal,
      (id) => { capturedSessionId = id },
    )

    expect(capturedSessionId).toBe('session-abc')
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
    await client.connect({}, callbacks, ctrl.signal)

    expect(callbacks.on_session_init).toHaveBeenCalledWith({ chat_id: 'c1' })
    expect(callbacks.on_assistant).toHaveBeenCalledWith({ delta: 'Hello' })
    expect(callbacks.on_assistant_done).toHaveBeenCalledWith({})
    expect(callbacks.on_done).toHaveBeenCalledWith({})
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
    await client.connect({}, callbacks, ctrl.signal)

    expect(callbacks.on_tool_call).toHaveBeenCalledWith({
      call_id: 'tc-1',
      tool_name: 'read_file',
      params: { path: '/tmp/test' },
      is_read_only: true,
      server: 'filesystem',
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
    await client.connect({}, callbacks, ctrl.signal)

    expect(callbacks.on_tool_result).toHaveBeenCalledWith({
      call_id: 'tc-1',
      execution_status: 'SUCCEEDED',
      output: { bytes_written: 42 },
      execution_time_ms: 150,
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
    await client.connect({}, callbacks, ctrl.signal)

    expect(callbacks.on_tool_approval_required).toHaveBeenCalledWith({
      chat_id: 'c1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs approval',
      call_id: 'tc-1',
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
    await client.connect({}, callbacks, ctrl.signal)

    expect(callbacks.on_error).toHaveBeenCalledWith({
      code: 'AGENT_CRASH',
      message: 'boom',
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
    await client.connect({}, callbacks, ctrl.signal)

    // Malformed event skipped, valid event processed
    expect(callbacks.on_assistant).toHaveBeenCalledTimes(1)
    expect(callbacks.on_assistant).toHaveBeenCalledWith({ delta: 'ok' })
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
    await client.connect({}, callbacks, ctrl.signal)

    // Only the valid event should trigger
    expect(callbacks.on_done).toHaveBeenCalledTimes(1)
  })

  it('onerror dispatches NETWORK_ERROR to on_error callback and rejects', async () => {
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
      client.connect({}, callbacks, ctrl.signal),
    ).rejects.toThrow('Connection lost')

    // The on_error callback should have been invoked before the throw
    expect(callbacks.on_error).toHaveBeenCalledWith({
      code: 'NETWORK_ERROR',
      message: 'Error: Connection lost',
    })
  })

  it('onopen without X-Session-ID does not call onSessionId', async () => {
    const fakeResponse = {
      headers: {
        get: vi.fn(() => null),
      },
    }

    mockFetchEventSource.mockImplementation(
      (_url: string, options: any) => {
        options.onopen(fakeResponse)
        return Promise.resolve()
      },
    )

    const ctrl = new AbortController()
    const onSessionId = vi.fn()

    await client.connect({}, callbacks, ctrl.signal, onSessionId)

    expect(onSessionId).not.toHaveBeenCalled()
  })
})
