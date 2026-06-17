import { inject } from 'vue'
import { SessionListStore } from '@/application/session-list-store'

export function useSessionList() {
  const store = inject<SessionListStore>('sessionListStore')!

  return {
    sessions: store.sessions,
    activeChatId: store.activeChatId,
    isLoading: store.isLoadingSessions,
    error: store.loadError,
  }
}
