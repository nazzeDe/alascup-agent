import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { SseClient } from '@/application/ports'
import type { ChatStreamEvent, ErrorEvent, SSEEventType } from '@/domain/sse-events'

const STREAM_EVENT_TYPES = new Set<SSEEventType>([
  'assistant',
  'assistant_done',
  'reasoning',
  'thinking_done',
  'tool_call',
  'tool_result',
  'tool_approval_required',
  'session_init',
  'error',
  'done',
])

function parseStreamEvent(event: string, data: string): ChatStreamEvent | null {
  if (!STREAM_EVENT_TYPES.has(event as SSEEventType)) return null
  try {
    return {
      type: event as ChatStreamEvent['type'],
      data: JSON.parse(data),
    } as ChatStreamEvent
  } catch {
    return null
  }
}

export class FetchEventSourceClient implements SseClient {
  connect(
    body: Record<string, unknown>,
    onEvent: (event: ChatStreamEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    return fetchEventSource('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal,
      openWhenHidden: true,
      onmessage(msg) {
        if (!msg.event || !msg.data) return
        const event = parseStreamEvent(msg.event, msg.data)
        if (event) onEvent(event)
      },
      onerror(err) {
        const errorEvent: ErrorEvent = {
          code: 'NETWORK_ERROR',
          message: String(err),
        }
        onEvent({ type: 'error', data: errorEvent })
        throw err
      },
    })
  }
}
