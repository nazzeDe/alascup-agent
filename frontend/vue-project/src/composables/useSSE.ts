import { ref, type Ref } from 'vue'
import type { SSECallbacks, SSEEventType } from '@/types'

interface SSEOptions {
  chatId?: string
  message: string
  model?: string
  maxTurns?: number
}

export function useSSE() {
  const isStreaming: Ref<boolean> = ref(false)
  let abortController: AbortController | null = null

  async function connect(options: SSEOptions, callbacks: SSECallbacks): Promise<void> {
    abortController = new AbortController()
    isStreaming.value = true

    const body: Record<string, unknown> = { message: options.message }
    if (options.chatId) body.chat_id = options.chatId
    if (options.model) body.model = options.model
    if (options.maxTurns) body.max_turns = options.maxTurns

    try {
      const response = await fetch('/api/chat-turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify(body),
        signal: abortController.signal,
      })

      if (!response.ok) {
        callbacks.onError?.({ code: 'HTTP_ERROR', message: `Server returned ${response.status}` })
        return
      }

      const reader = response.body?.getReader() ?? null
      if (!reader) {
        callbacks.onError?.({ code: 'STREAM_ERROR', message: 'No response body' })
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        let currentEvent: SSEEventType | '' = ''
        let currentData = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim() as SSEEventType
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6)
          } else if (line === '' && currentEvent && currentData) {
            try {
              const parsed = JSON.parse(currentData)
              dispatchEvent(currentEvent, parsed, callbacks)
            } catch { /* skip malformed */ }
            currentEvent = ''
            currentData = ''
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      callbacks.onError?.({
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

function dispatchEvent(type: SSEEventType, data: unknown, callbacks: SSECallbacks): void {
  switch (type) {
    case 'assistant': callbacks.onAssistant?.(data as never); break
    case 'tool_call': callbacks.onToolCall?.(data as never); break
    case 'tool_result': callbacks.onToolResult?.(data as never); break
    case 'tool_approval_required': callbacks.onToolApprovalRequired?.(data as never); break
    case 'error': callbacks.onError?.(data as never); break
    case 'done': callbacks.onDone?.(data as never); break
  }
}
