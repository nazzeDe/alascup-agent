import type { ChatSession } from '@/domain/models'
import { ref, type Ref } from 'vue'

export class SessionListStore {
  readonly sessions: Ref<ChatSession[]> = ref([])
  readonly activeChatId: Ref<string | null> = ref(null)
  readonly isLoadingSessions: Ref<boolean> = ref(false)
  readonly loadError: Ref<string> = ref('')

  setSessions(list: ChatSession[]): void {
    this.sessions.value = list
  }

  addSession(session: ChatSession): void {
    this.sessions.value = [session, ...this.sessions.value]
  }

  removeSession(chatId: string): void {
    this.sessions.value = this.sessions.value.filter(s => s.chat_id !== chatId)
  }

  setActive(chatId: string | null): void {
    this.activeChatId.value = chatId
  }

  setLoading(v: boolean): void {
    this.isLoadingSessions.value = v
  }

  setError(msg: string): void {
    this.loadError.value = msg
  }
}
