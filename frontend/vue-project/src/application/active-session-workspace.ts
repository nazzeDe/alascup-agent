import { shallowRef, type Ref } from 'vue'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { SessionService } from '@/application/session-service'

export class ActiveSessionWorkspace {
  readonly activeChatStore: Ref<ChatStore> = shallowRef(new ChatStore())
  private selectionVersion = 0

  constructor(
    private readonly sessionService: SessionService,
    private readonly sessionListStore: SessionListStore,
  ) {}

  loadSessions(): Promise<void> {
    return this.sessionService.loadSessions()
  }

  async select(chatId: string): Promise<void> {
    const version = ++this.selectionVersion
    const previousActiveChatId = this.sessionListStore.activeChatId.value
    this.sessionListStore.setActive(chatId)
    const newStore = new ChatStore()
    newStore.chatId.value = chatId
    const loaded = await this.sessionService.loadHistory(chatId, newStore)
    if (!loaded) {
      if (version === this.selectionVersion) {
        this.sessionListStore.setActive(previousActiveChatId)
      }
      return
    }
    if (
      newStore.chatId.value !== chatId
      || version !== this.selectionVersion
      || this.sessionListStore.activeChatId.value !== chatId
    ) {
      return
    }
    this.activeChatStore.value = newStore
  }

  createDraftSession(): void {
    this.selectionVersion += 1
    this.sessionListStore.setActive(null)
    this.activeChatStore.value = new ChatStore()
  }

  async delete(chatId: string): Promise<void> {
    await this.sessionService.deleteSession(chatId)
    if (this.sessionListStore.activeChatId.value === null) {
      this.selectionVersion += 1
      this.activeChatStore.value = new ChatStore()
    }
  }

  adoptServerSession(chatId: string, title: string, targetStore = this.activeChatStore.value): void {
    const store = targetStore
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
    if (this.activeChatStore.value === store) {
      this.sessionListStore.setActive(chatId)
    }
  }
}
