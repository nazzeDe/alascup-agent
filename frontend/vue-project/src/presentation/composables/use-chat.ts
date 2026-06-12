import { computed, inject, onUnmounted, type Ref } from 'vue'
import { ChatStore } from '@/application/chat-store'
import type { SessionListStore } from '@/application/session-list-store'
import type { SseClient } from '@/application/ports'
import type { Message, ToolCallInfo } from '@/domain/models'

/**
 * Per-session SSE chat composable.
 *
 * Injects SseClient + ChatStore + SessionListStore from AppShell via provide/inject.
 * Owns the full SSE lifecycle: connect, dispatch events → ChatStore, abort.
 */
export function useChat() {
  const sseClient = inject<SseClient>('sseClient')!
  const storeRef = inject<Ref<ChatStore>>('chatStore')!
  const sessionListStore = inject<SessionListStore>('sessionListStore')!

  let abortController: AbortController | null = null
  let toolTimeout: ReturnType<typeof setTimeout> | null = null
  let currentChatId: string | null = null

  function sendMessage(text: string): void {
    const store = storeRef.value
    const isNewChat = currentChatId === null

    store.setApprovalEvent(null)
    store.setConnectionError(null)

    const userMsg: Message = {
      message_id: crypto.randomUUID(),
      chat_id: currentChatId ?? '',
      timestamp: new Date().toISOString(),
      type: 'user',
      content: text,
    }
    store.addMessage(userMsg)
    store.clearReasonings()
    store.setPhase('thinking')
    store.setStreaming(true)

    abortController = new AbortController()

    let sessionChatId = currentChatId ?? ''
    let currentReasoningId = ''
    let reasoningBuffer = ''
    let assistantBuffer = ''
    let newChatId: string | null = null

    sseClient.connect(
      { chat_id: currentChatId ?? undefined, message: text },
      {
        on_session_init(data: any) {
          sessionChatId = data.chat_id
          if (isNewChat) newChatId = data.chat_id
        },

        on_reasoning(data: any) {
          const delta = data.delta
          if (!currentReasoningId) {
            currentReasoningId = crypto.randomUUID()
            reasoningBuffer = ''
          }
          reasoningBuffer += delta
          _updateReasoningContent(store, currentReasoningId, reasoningBuffer)
        },

        on_thinking_done(_data: any) {
          if (currentReasoningId) {
            store.markReasoningDone(currentReasoningId)
            currentReasoningId = ''
            reasoningBuffer = ''
          }
          store.setPhase('responding')
        },

        on_assistant(data: any) {
          if (currentReasoningId) {
            store.markReasoningDone(currentReasoningId)
            currentReasoningId = ''
            reasoningBuffer = ''
          }
          store.setPhase('responding')
          assistantBuffer += data.delta
        },

        on_assistant_done(_data: any) {
          if (assistantBuffer) {
            const msg: Message = {
              message_id: crypto.randomUUID(),
              chat_id: sessionChatId,
              timestamp: new Date().toISOString(),
              type: 'assistant',
              content: assistantBuffer,
            }
            store.addMessage(msg)
            assistantBuffer = ''
          }
        },

        on_tool_call(data: any) {
          console.log('[SSE] on_tool_call', data.call_id, data.tool_name, 'is_read_only:', data.is_read_only)
          store.setPhase('calling_tool')

          const tc: ToolCallInfo = {
            call_id: data.call_id,
            chat_id: sessionChatId,
            tool_name: data.tool_name,
            server: data.server,
            is_read_only: data.is_read_only,
            params: data.params,
            execution_status: 'RUNNING',
            timestamp: new Date().toISOString(),
          }
          store.setToolCall(data.call_id, tc)

          if (toolTimeout) clearTimeout(toolTimeout)
          toolTimeout = setTimeout(() => {
            if (store.agentPhase.value === 'calling_tool') {
              store.setPhase('waiting_for_tool')
            }
          }, 800)
        },

        on_tool_result(data: any) {
          console.log('[SSE] on_tool_result', data.call_id, data.execution_status)
          store.setPhase('thinking')
          if (toolTimeout) { clearTimeout(toolTimeout); toolTimeout = null }
          const existing = store.toolCalls.value.get(data.call_id)
          if (!existing) {
            console.warn('[SSE] on_tool_result: unknown call_id', data.call_id)
          }
          if (existing) {
            const updated = new Map(store.toolCalls.value)
            updated.set(data.call_id, {
              ...existing,
              execution_status: data.execution_status,
              output: data.output,
              execution_time_ms: data.execution_time_ms,
              ...(data.error ? { error: data.error } : {}),
            })
            store.toolCalls.value = updated
          }
        },

        on_tool_approval_required(data: any) {
          store.setApprovalEvent({
            request_id: data.request_id,
            tool_name: data.tool_name,
            params: data.params,
            reason: data.reason,
            status: 'pending',
            message: '',
          })
          store.setPhase('awaiting_approval')
          if (data.call_id) {
            const existing = store.toolCalls.value.get(data.call_id)
            if (existing && existing.execution_status === 'RUNNING') {
              const updated = new Map(store.toolCalls.value)
              updated.set(data.call_id, { ...existing, execution_status: 'PENDING_APPROVAL' })
              store.toolCalls.value = updated
            }
          }
        },

        on_done(_data: any) {
          const runningTools = [...store.toolCalls.value.values()].filter(
            tc => tc.execution_status === 'RUNNING'
          )
          if (runningTools.length > 0) {
            console.warn('[SSE] on_done: force-failing RUNNING tools:', runningTools.map(tc => `${tc.call_id}(${tc.tool_name})`))
          } else {
            console.log('[SSE] on_done: no RUNNING tools, clean exit')
          }
          store.setPhase('done')
          store.setStreaming(false)
          if (!store.connectionError.value) {
            store.setConnectionError(null)
          }
          // Force-fail RUNNING tools, skip PENDING_APPROVAL
          const updated = new Map(store.toolCalls.value)
          for (const [id, tc] of updated) {
            if (tc.execution_status === 'RUNNING') {
              console.warn('[SSE] on_done: force-failing', id, tc.tool_name)
              updated.set(id, {
                ...tc,
                execution_status: 'FAILED',
                error: { message: 'Connection closed before tool completed' },
              } as ToolCallInfo)
            }
          }
          store.toolCalls.value = updated
          if (isNewChat && newChatId) {
            currentChatId = newChatId
            sessionListStore.addSession({
              chat_id: newChatId,
              messages: store.messages.value,
              executed_tool_list: [],
              timestamp: new Date().toISOString(),
            })
            sessionListStore.setActive(newChatId)
          }
        },

        on_error(data: any) {
          store.setStreaming(false)
          store.setPhase('idle')
          store.setConnectionError({
            code: data.code || 'UNKNOWN',
            message: data.message || String(data),
          })
        },
      },
      abortController.signal,
      (sid: string) => { if (isNewChat) newChatId = sid },
    ).then(() => {
      if (store.isStreaming.value) {
        store.setStreaming(false)
        store.setPhase('idle')
      }
    }).catch((err: unknown) => {
      store.setStreaming(false)
      if (err instanceof DOMException && err.name === 'AbortError') return
      store.setConnectionError({
        code: 'NETWORK_ERROR',
        message: err instanceof Error ? err.message : 'Connection lost',
      })
    })
  }

  function abort(): void {
    abortController?.abort()
    const store = storeRef.value
    store.setStreaming(false)
    store.setPhase('idle')
    store.reasonings.value = store.reasonings.value.map(r => ({ ...r, done: true }))
  }

  onUnmounted(() => abort())

  // Reactive state — computed through storeRef so they stay reactive
  // when AppShell replaces the active ChatStore (shallowRef swap).
  return {
    messages:         computed(() => storeRef.value.messages.value),
    toolCalls:        computed(() => storeRef.value.toolCalls.value),
    reasonings:       computed(() => storeRef.value.reasonings.value),
    isStreaming:      computed(() => storeRef.value.isStreaming.value),
    draftInput:       computed({
      get: ()  => storeRef.value.draftInput.value,
      set: (v: string) => { storeRef.value.draftInput.value = v },
    }),
    approvalEvent:    computed(() => storeRef.value.approvalEvent.value),
    agentPhase:       computed(() => storeRef.value.agentPhase.value),
    phaseLabel:       computed(() => storeRef.value.phaseLabel.value),
    isLoadingHistory: computed(() => storeRef.value.isLoadingHistory.value),
    connectionError:  computed(() => storeRef.value.connectionError.value),
    send: sendMessage,
    abort,
    setChatId: (id: string | null) => { currentChatId = id },
  }
}

/** Replace the full content of a reasoning entry (matching old behavior). */
function _updateReasoningContent(store: ChatStore, messageId: string, fullContent: string): void {
  const reasonings = store.reasonings.value
  const idx = reasonings.findIndex(r => r.message_id === messageId)
  if (idx !== -1) {
    const updated = [...reasonings]
    updated[idx] = { ...updated[idx]!, content: fullContent }
    store.reasonings.value = updated
  } else {
    store.reasonings.value = [...reasonings, {
      message_id: messageId,
      content: fullContent,
      done: false,
      timestamp: new Date().toISOString(),
    }]
  }
}
