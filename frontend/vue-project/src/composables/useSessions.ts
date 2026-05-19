import { ref, type Ref } from 'vue'
import type { ChatSession } from '@/types'

const sessions: Ref<ChatSession[]> = ref([])
const activeChatId: Ref<string | undefined> = ref(undefined)
const isLoadingSessions: Ref<boolean> = ref(false)
const isCreatingSession: Ref<boolean> = ref(false)

export function useSessions() {
  async function loadSessions(): Promise<void> {
    isLoadingSessions.value = true
    try {
      const res = await fetch('/api/sessions')
      if (!res.ok) return
      sessions.value = await res.json()
    } catch {
      // silently fail
    } finally {
      isLoadingSessions.value = false
    }
  }

  async function createSession(): Promise<string | undefined> {
    isCreatingSession.value = true
    try {
      const res = await fetch('/api/sessions', { method: 'POST' })
      if (!res.ok) return undefined
      const session: ChatSession = await res.json()
      sessions.value = [session, ...sessions.value]
      activeChatId.value = session.chatID
      return session.chatID
    } catch {
      return undefined
    } finally {
      isCreatingSession.value = false
    }
  }

  async function switchSession(chatId: string): Promise<void> {
    activeChatId.value = chatId
  }

  return {
    sessions,
    activeChatId,
    isLoadingSessions,
    isCreatingSession,
    loadSessions,
    createSession,
    switchSession,
  }
}
