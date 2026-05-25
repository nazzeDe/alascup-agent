import { ref, type Ref } from 'vue'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { SSECallbacks, SSEEventType } from '@/types'

interface SSEOptions {
  chat_id?: string
  message: string
  model?: string
  max_turns?: number
}

export function useSSE() {
  const isStreaming: Ref<boolean> = ref(false)
  let abortController: AbortController | null = null

  async function connect(options: SSEOptions, callbacks: SSECallbacks): Promise<void> {
    abortController = new AbortController()
    isStreaming.value = true

    const body: Record<string, unknown> = { message: options.message }
    if (options.chat_id) body.chat_id = options.chat_id
    if (options.model) body.model = options.model
    if (options.max_turns) body.max_turns = options.max_turns

    try {
      await fetchEventSource('/api/chat-turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify(body),
        signal: abortController.signal,
        openWhenHidden: true,
        onmessage(msg) {
          if (!msg.event || !msg.data) return
          try {
            const parsed = JSON.parse(msg.data)
            dispatch_event(msg.event as SSEEventType, parsed, callbacks)
          } catch {
            console.warn('SSE: malformed event data', msg.data)
          }
        },
        onerror(err) {
          isStreaming.value = false
          abortController = null
          callbacks.on_error?.({ code: 'NETWORK_ERROR', message: String(err) })
          throw err
        },
      })
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      callbacks.on_error?.({
        code: 'NETWORK_ERROR',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    } finally {
      isStreaming.value = false
      abortController = null
    }
  }

  function abort(): void {
    abortController?.abort()
    isStreaming.value = false
  }

  return { connect, abort, isStreaming }
}

function dispatch_event(type: SSEEventType, data: unknown, callbacks: SSECallbacks): void {
  switch (type) {
    case 'reasoning': callbacks.on_reasoning?.(data as never); break
    case 'assistant': callbacks.on_assistant?.(data as never); break
    case 'tool_call': callbacks.on_tool_call?.(data as never); break
    case 'tool_result': callbacks.on_tool_result?.(data as never); break
    case 'tool_approval_required': callbacks.on_tool_approval_required?.(data as never); break
    case 'error': callbacks.on_error?.(data as never); break
    case 'done': callbacks.on_done?.(data as never); break
  }
}
