import { ref, type Ref } from 'vue'
import type { ChatSession } from '@/types'

const sessions: Ref<ChatSession[]> = ref([])
const activeChatId: Ref<string | undefined> = ref(undefined)
const isLoadingSessions: Ref<boolean> = ref(false)
const isCreatingSession: Ref<boolean> = ref(false)
const loadError: Ref<string> = ref('')

export function useSessions() {
  async function loadSessions(): Promise<void> {
    isLoadingSessions.value = true
    loadError.value = ''
    try {
      const res = await fetch('/api/sessions')
      if (!res.ok) {
        loadError.value = `Server error (${res.status})`
        return
      }
      sessions.value = await res.json()
    } catch {
      loadError.value = 'Cannot connect to server'
    } finally {
      isLoadingSessions.value = false
    }
  }

  async function createSession(): Promise<string | undefined> {
    isCreatingSession.value = true
    loadError.value = ''
    try {
      const res = await fetch('/api/sessions', { method: 'POST' })
      if (!res.ok) {
        loadError.value = `Server error (${res.status})`
        return undefined
      }
      const session: ChatSession = await res.json()
      sessions.value = [session, ...sessions.value]
      activeChatId.value = session.chat_id
      return session.chat_id
    } catch {
      loadError.value = 'Cannot connect to server'
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
    loadError,
    loadSessions,
    createSession,
    switchSession,
  }
}
