import { describe, it, expect, beforeEach } from 'vitest'
import { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import { SessionService } from '@/application/session-service'
import { SessionListStore } from '@/application/session-list-store'
import type { ApprovalApi, SessionApi } from '@/application/ports'
import type { ChatSession } from '@/domain/models'

class FakeSessionApi implements SessionApi {
  sessions = new Map<string, ChatSession>()
  private blockers = new Map<string, Promise<void>>()

  async listSessions(): Promise<ChatSession[]> {
    return [...this.sessions.values()]
  }

  async getSession(chatId: string): Promise<ChatSession> {
    await this.blockers.get(chatId)
    const session = this.sessions.get(chatId)
    if (!session) throw new Error('missing')
    return session
  }

  async deleteSession(chatId: string): Promise<void> {
    this.sessions.delete(chatId)
  }

  blockGet(chatId: string, blocker: Promise<void>): void {
    this.blockers.set(chatId, blocker)
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

  it('ignores stale history responses from earlier session selections', async () => {
    let releaseSlow!: () => void
    sessionApi.sessions.set('slow-chat', {
      chat_id: 'slow-chat',
      messages: [{ message_id: 'slow-m1', chat_id: 'slow-chat', timestamp: 't', type: 'user', content: 'slow' }],
      executed_tool_list: [],
      timestamp: 't',
    })
    sessionApi.sessions.set('fast-chat', {
      chat_id: 'fast-chat',
      messages: [{ message_id: 'fast-m1', chat_id: 'fast-chat', timestamp: 't', type: 'user', content: 'fast' }],
      executed_tool_list: [],
      timestamp: 't',
    })
    sessionApi.blockGet('slow-chat', new Promise<void>(resolve => { releaseSlow = resolve }))

    const slowSelect = workspace.select('slow-chat')
    await workspace.select('fast-chat')
    releaseSlow()
    await slowSelect

    expect(sessionListStore.activeChatId.value).toBe('fast-chat')
    expect(workspace.activeChatStore.value.chatId.value).toBe('fast-chat')
    expect(workspace.activeChatStore.value.messages.value[0]!.content).toBe('fast')
  })

  it('keeps the previous active session when selected history fails to load', async () => {
    sessionApi.sessions.set('current-chat', {
      chat_id: 'current-chat',
      messages: [{ message_id: 'current-m1', chat_id: 'current-chat', timestamp: 't', type: 'user', content: 'current' }],
      executed_tool_list: [],
      timestamp: 't',
    })
    await workspace.select('current-chat')

    await workspace.select('missing-chat')

    expect(sessionListStore.activeChatId.value).toBe('current-chat')
    expect(workspace.activeChatStore.value.chatId.value).toBe('current-chat')
    expect(workspace.activeChatStore.value.messages.value[0]!.content).toBe('current')
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
