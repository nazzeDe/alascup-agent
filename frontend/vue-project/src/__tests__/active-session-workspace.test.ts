import { describe, it, expect, beforeEach } from 'vitest'
import { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import { SessionService } from '@/application/session-service'
import { SessionListStore } from '@/application/session-list-store'
import type { ApprovalApi, SessionApi } from '@/application/ports'
import type { ChatSession } from '@/domain/models'

class FakeSessionApi implements SessionApi {
  sessions = new Map<string, ChatSession>()

  async listSessions(): Promise<ChatSession[]> {
    return [...this.sessions.values()]
  }

  async getSession(chatId: string): Promise<ChatSession> {
    const session = this.sessions.get(chatId)
    if (!session) throw new Error('missing')
    return session
  }

  async deleteSession(chatId: string): Promise<void> {
    this.sessions.delete(chatId)
  }
}

const approvalApi: ApprovalApi = {
  approve: async () => {},
  reject: async () => {},
}

describe('ActiveSessionWorkspace', () => {
  let sessionApi: FakeSessionApi
  let sessionListStore: SessionListStore
  let workspace: ActiveSessionWorkspace

  beforeEach(() => {
    sessionApi = new FakeSessionApi()
    sessionListStore = new SessionListStore()
    workspace = new ActiveSessionWorkspace(
      new SessionService(sessionApi, approvalApi, sessionListStore),
      sessionListStore,
    )
  })

  it('selects a session and swaps the active chat store', async () => {
    sessionApi.sessions.set('chat-1', {
      chat_id: 'chat-1',
      messages: [{ message_id: 'm1', chat_id: 'chat-1', timestamp: 't', type: 'user', content: 'hi' }],
      executed_tool_list: [],
      timestamp: 't',
    })

    await workspace.select('chat-1')

    expect(sessionListStore.activeChatId.value).toBe('chat-1')
    expect(workspace.activeChatStore.value.chatId.value).toBe('chat-1')
    expect(workspace.activeChatStore.value.messages.value).toHaveLength(1)
  })

  it('creates a draft session with a fresh store', () => {
    workspace.activeChatStore.value.chatId.value = 'chat-1'
    sessionListStore.setActive('chat-1')

    workspace.createDraftSession()

    expect(sessionListStore.activeChatId.value).toBeNull()
    expect(workspace.activeChatStore.value.chatId.value).toBeNull()
  })

  it('adopts server session once for a new chat', () => {
    workspace.adoptServerSession('chat-2', 'first prompt')
    workspace.adoptServerSession('chat-2', 'first prompt')

    expect(workspace.activeChatStore.value.chatId.value).toBe('chat-2')
    expect(sessionListStore.activeChatId.value).toBe('chat-2')
    expect(sessionListStore.sessions.value).toHaveLength(1)
  })

  it('adopts a new chat into the originating store without polluting the active store', async () => {
    sessionApi.sessions.set('chat-existing', {
      chat_id: 'chat-existing',
      messages: [{ message_id: 'm1', chat_id: 'chat-existing', timestamp: 't', type: 'user', content: 'old' }],
      executed_tool_list: [],
      timestamp: 't',
    })
    const originatingStore = workspace.activeChatStore.value

    await workspace.select('chat-existing')
    workspace.adoptServerSession('chat-new', 'first prompt', originatingStore)

    expect(originatingStore.chatId.value).toBe('chat-new')
    expect(workspace.activeChatStore.value.chatId.value).toBe('chat-existing')
    expect(sessionListStore.activeChatId.value).toBe('chat-existing')
    expect(sessionListStore.sessions.value.some(s => s.chat_id === 'chat-new')).toBe(true)
  })

  it('deleting the active session leaves a fresh empty store', async () => {
    sessionListStore.setSessions([{ chat_id: 'chat-1', messages: [], executed_tool_list: [], timestamp: 't' }])
    sessionListStore.setActive('chat-1')
    workspace.activeChatStore.value.chatId.value = 'chat-1'

    await workspace.delete('chat-1')

    expect(sessionListStore.activeChatId.value).toBeNull()
    expect(workspace.activeChatStore.value.chatId.value).toBeNull()
  })
})
