import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { SseClient } from '@/application/ports'
import type { SSECallbacks, ErrorEvent } from '@/domain/sse-events'

export class FetchEventSourceClient implements SseClient {
  connect(
    body: Record<string, unknown>,
    callbacks: SSECallbacks,
    signal: AbortSignal,
    onSessionId?: (chatId: string) => void,
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
      onopen(response) {
        const sid = response.headers.get('X-Session-ID')
        if (sid) onSessionId?.(sid)
      },
      onmessage(msg) {
        if (!msg.event || !msg.data) return
        try {
          const parsed = JSON.parse(msg.data)
          const cb = callbacks[`on_${msg.event}` as keyof SSECallbacks]
          cb?.(parsed as never)
        } catch {
          /* malformed event, skip */
        }
      },
      onerror(err) {
        const errorEvent: ErrorEvent = {
          code: 'NETWORK_ERROR',
          message: String(err),
        }
        callbacks.on_error?.(errorEvent)
        throw err
      },
    })
  }
}
