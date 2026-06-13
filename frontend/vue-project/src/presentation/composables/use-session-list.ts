import { inject, onMounted, type Ref } from 'vue'
import { SessionListStore } from '@/application/session-list-store'
import { ChatStore } from '@/application/chat-store'
import type { SessionService } from '@/application/session-service'

export function useSessionList() {
  const store = inject<SessionListStore>('sessionListStore')!
  const chatStoreRef = inject<Ref<ChatStore>>('chatStore')!
  const service = inject<SessionService>('sessionService')!

  onMounted(() => service.loadSessions())

  return {
    sessions: store.sessions,
    activeChatId: store.activeChatId,
    isLoading: store.isLoadingSessions,
    error: store.loadError,
    select: (chatId: string) => service.loadHistory(chatId, chatStoreRef.value),
    delete: (chatId: string) => service.deleteSession(chatId),
  }
}
