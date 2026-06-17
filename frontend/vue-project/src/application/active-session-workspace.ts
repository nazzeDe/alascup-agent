import { shallowRef, type Ref } from 'vue'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { SessionService } from '@/application/session-service'

export class ActiveSessionWorkspace {
  readonly activeChatStore: Ref<ChatStore> = shallowRef(new ChatStore())

  constructor(
    private readonly sessionService: SessionService,
    private readonly sessionListStore: SessionListStore,
  ) {}

  loadSessions(): Promise<void> {
    return this.sessionService.loadSessions()
  }

  async select(chatId: string): Promise<void> {
    this.sessionListStore.setActive(chatId)
    const newStore = new ChatStore()
    newStore.chatId.value = chatId
    await this.sessionService.loadHistory(chatId, newStore)
    this.activeChatStore.value = newStore
  }

  createDraftSession(): void {
    this.sessionListStore.setActive(null)
    this.activeChatStore.value = new ChatStore()
  }

  async delete(chatId: string): Promise<void> {
    await this.sessionService.deleteSession(chatId)
    if (this.sessionListStore.activeChatId.value === null) {
      this.activeChatStore.value = new ChatStore()
    }
  }

  adoptServerSession(chatId: string, title: string): void {
    const store = this.activeChatStore.value
    store.chatId.value = chatId
    if (!this.sessionListStore.sessions.value.some(s => s.chat_id === chatId)) {
      this.sessionListStore.addSession({
        chat_id: chatId,
        title,
        messages: [],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      })
    }
    this.sessionListStore.setActive(chatId)
  }
}
