import { computed, inject, onUnmounted, type Ref } from 'vue'
import { ChatStore } from '@/application/chat-store'
import type { ToastStore } from '@/application/toast-store'
import type { SseClient } from '@/application/ports'
import { ChatStreamInterpreter } from '@/application/chat-stream-interpreter'
import type { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import type { Message } from '@/domain/models'

/**
 * Per-session SSE chat composable.
 *
 * Injects transport + active session state from AppShell via provide/inject.
 * Owns the SSE lifecycle; ChatStreamInterpreter owns event interpretation.
 */
export function useChat() {
  const sseClient = inject<SseClient>('sseClient')!
  const storeRef = inject<Ref<ChatStore>>('chatStore')!
  const toastStore = inject<ToastStore>('toastStore')!
  const activeSessionWorkspace = inject<ActiveSessionWorkspace>('activeSessionWorkspace')!

  let abortController: AbortController | null = null
  let streamInterpreter: ChatStreamInterpreter | null = null

  function sendMessage(text: string): void {
    const store = storeRef.value
    const isNewChat = store.chatId.value === null

    store.setApprovalEvent(null)
    store.setConnectionError(null)

    const userMsg: Message = {
      message_id: crypto.randomUUID(),
      chat_id: store.chatId.value ?? '',
      timestamp: new Date().toISOString(),
      type: 'user',
      content: text,
    }
    store.addMessage(userMsg)
    store.clearReasonings()
    store.setPhase('thinking')
    store.setStreaming(true)

    abortController = new AbortController()

    const firstMessageTitle = text.length > 30 ? text.slice(0, 30) + '...' : text
    streamInterpreter = new ChatStreamInterpreter({
      chatStore: store,
      activeSessionWorkspace,
      toastStore,
      isNewChat,
      firstMessageTitle,
    })

    sseClient.connect(
      { chat_id: store.chatId.value ?? undefined, message: text },
      event => streamInterpreter?.apply(event),
      abortController.signal,
    ).then(() => {
      streamInterpreter?.completeConnection()
    }).catch((err: unknown) => {
      store.setStreaming(false)
      if (err instanceof DOMException && err.name === 'AbortError') return
      store.setConnectionError({
        code: 'NETWORK_ERROR',
        message: err instanceof Error ? err.message : 'Connection lost',
      })
      if (isNewChat) {
        toastStore.show('error', '发送失败，请刷新页面重试')
      }
    })
  }

  function abort(): void {
    abortController?.abort()
    streamInterpreter?.abort()
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
  }
}
