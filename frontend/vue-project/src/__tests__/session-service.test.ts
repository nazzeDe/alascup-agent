import { describe, it, expect, beforeEach } from 'vitest'
import { SessionService } from '@/application/session-service'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import type { SessionApi, ApprovalApi } from '@/application/ports'
import type { ChatSession, Message, ToolCallInfo, ApprovalEvent } from '@/domain/models'

// FakeSessionApi
class FakeSessionApi implements SessionApi {
  private _sessions: ChatSession[] = []
  private _sessionMap: Map<string, ChatSession> = new Map()
  private _nextId = 1
  private _failOnList = false
  private _failOnGet = false
  private _failOnDelete = false

  // Config helpers
  setFailOnList(v: boolean) { this._failOnList = v }
  setSessions(sessions: ChatSession[]) {
    this._sessions = sessions
    for (const s of sessions) { this._sessionMap.set(s.chat_id, s) }
  }
  addSession(session: ChatSession) {
    this._sessions.push(session)
    this._sessionMap.set(session.chat_id, session)
  }

  async listSessions(): Promise<ChatSession[]> {
    if (this._failOnList) throw new Error('Network error')
    return this._sessions
  }

  async getSession(chatId: string): Promise<ChatSession> {
    if (this._failOnGet) throw new Error('Not found')
    const session = this._sessionMap.get(chatId)
    if (!session) throw new Error('Session not found')
    return session
  }

  async deleteSession(_chatId: string): Promise<void> {
    if (this._failOnDelete) throw new Error('Delete failed')
  }
}

// FakeApprovalApi
class FakeApprovalApi implements ApprovalApi {
  private _lastRequestId: string | null = null
  private _lastChatId: string | null = null
  private _lastReason: string | null = null
  private _lastApproved: boolean | null = null
  private _failOnApprove = false
  private _failOnReject = false

  setFailOnApprove(v: boolean) { this._failOnApprove = v }
  setFailOnReject(v: boolean) { this._failOnReject = v }
  get lastRequestId() { return this._lastRequestId }
  get lastChatId() { return this._lastChatId }
  get lastReason() { return this._lastReason }
  get lastApproved() { return this._lastApproved }

  async approve(requestId: string, chatId: string, reason?: string): Promise<void> {
    if (this._failOnApprove) throw new Error('Approve failed')
    this._lastRequestId = requestId
    this._lastChatId = chatId
    this._lastReason = reason ?? ''
    this._lastApproved = true
  }

  async reject(requestId: string, chatId: string, reason?: string): Promise<void> {
    if (this._failOnReject) throw new Error('Reject failed')
    this._lastRequestId = requestId
    this._lastChatId = chatId
    this._lastReason = reason ?? ''
    this._lastApproved = false
  }
}

describe('SessionService', () => {
  let sessionApi: FakeSessionApi
  let approvalApi: FakeApprovalApi
  let sessionListStore: SessionListStore
  let chatStore: ChatStore
  let service: SessionService

  beforeEach(() => {
    sessionApi = new FakeSessionApi()
    approvalApi = new FakeApprovalApi()
    sessionListStore = new SessionListStore()
    chatStore = new ChatStore()
    service = new SessionService(sessionApi, approvalApi, sessionListStore)
  })

  // --- 1. loadSessions fetches and sets sessions ---
  it('loadSessions fetches and sets sessions', async () => {
    const sessions: ChatSession[] = [
      { chat_id: 's1', messages: [], executed_tool_list: [], timestamp: 't1' },
      { chat_id: 's2', messages: [], executed_tool_list: [], timestamp: 't2' },
    ]
    sessionApi.setSessions(sessions)

    await service.loadSessions()
    expect(sessionListStore.sessions.value).toHaveLength(2)
    expect(sessionListStore.sessions.value[0]!.chat_id).toBe('s1')
    expect(sessionListStore.sessions.value[1]!.chat_id).toBe('s2')
    expect(sessionListStore.isLoadingSessions.value).toBe(false)
    expect(sessionListStore.loadError.value).toBe('')
  })

  // --- 4. loadSessions handles error ---
  it('loadSessions handles error and sets loadError', async () => {
    sessionApi.setFailOnList(true)
    await service.loadSessions()
    expect(sessionListStore.loadError.value).toBe('Cannot connect to server')
    expect(sessionListStore.isLoadingSessions.value).toBe(false)
  })

  // --- 5. loadHistory with null clears chatStore ---
  it('loadHistory with null clears chatStore', async () => {
    // First put some data in chatStore
    chatStore.addMessage({
      message_id: 'm1', chat_id: 's1', timestamp: 't', type: 'user', content: 'hi',
    })
    const result = await service.loadHistory(null, chatStore)
    expect(result).toBe(true)
    expect(chatStore.messages.value).toEqual([])
    expect(chatStore.toolCalls.value.size).toBe(0)
    expect(chatStore.reasonings.value).toEqual([])
    expect(chatStore.isLoadingHistory.value).toBe(false)
  })

  // --- 6. loadHistory with chatId fetches and restores messages+tools ---
  it('loadHistory with chatId fetches and restores messages and tools', async () => {
    const msgs: Message[] = [
      { message_id: 'm1', chat_id: 's1', timestamp: 't1', type: 'user', content: 'hello' },
      { message_id: 'm2', chat_id: 's1', timestamp: 't2', type: 'assistant', content: 'hi' },
    ]
    const tcs: ToolCallInfo[] = [
      {
        call_id: 'tc1',
        chat_id: 's1',
        tool_name: 'read',
        is_read_only: true,
        execution_status: 'SUCCEEDED',
        timestamp: 't3',
      },
    ]
    sessionApi.addSession({
      chat_id: 's1',
      messages: msgs,
      executed_tool_list: tcs,
      timestamp: 't1',
    })

    const result = await service.loadHistory('s1', chatStore)
    expect(result).toBe(true)
    expect(chatStore.messages.value).toHaveLength(2)
    expect(chatStore.messages.value[0]!.content).toBe('hello')
    expect(chatStore.toolCalls.value.size).toBe(1)
    expect(chatStore.toolCalls.value.get('tc1')!.tool_name).toBe('read')
    expect(chatStore.reasonings.value).toEqual([])
    expect(chatStore.approvalEvent.value).toBeNull()
    expect(chatStore.agentPhase.value).toBe('idle')
  })

  it('normalizes rejected approval history from failed execution rows', async () => {
    sessionApi.addSession({
      chat_id: 's1',
      messages: [],
      executed_tool_list: [
        {
          call_id: 'tc-rejected',
          chat_id: 's1',
          tool_name: 'bash',
          is_read_only: false,
          approval_status: 'REJECTED',
          execution_status: 'FAILED',
          error: { message: 'Tool was rejected by human. Do NOT retry.' },
          timestamp: 't3',
        },
      ],
      timestamp: 't1',
    })

    await service.loadHistory('s1', chatStore)

    expect(chatStore.toolCalls.value.get('tc-rejected')!.execution_status).toBe('REJECTED')
  })

  // --- 7. deleteSession removes from store, clears active if matching ---
  it('deleteSession removes from store, clears active if matching', async () => {
    sessionListStore.setSessions([
      { chat_id: 's1', messages: [], executed_tool_list: [], timestamp: 't1' },
      { chat_id: 's2', messages: [], executed_tool_list: [], timestamp: 't2' },
    ])
    sessionListStore.setActive('s1')

    await service.deleteSession('s1')

    expect(sessionListStore.sessions.value).toHaveLength(1)
    expect(sessionListStore.sessions.value[0]!.chat_id).toBe('s2')
    expect(sessionListStore.activeChatId.value).toBeNull()
  })

  it('deleteSession does not clear active if different chatId', async () => {
    sessionListStore.setSessions([
      { chat_id: 's1', messages: [], executed_tool_list: [], timestamp: 't1' },
      { chat_id: 's2', messages: [], executed_tool_list: [], timestamp: 't2' },
    ])
    sessionListStore.setActive('s2')

    await service.deleteSession('s1')

    expect(sessionListStore.activeChatId.value).toBe('s2')
  })

  // --- 8. approve sends correct body, updates chatStore.approvalEvent ---
  it('approve sends correct body and updates approvalEvent', async () => {
    const ev: ApprovalEvent = {
      chat_id: 'chat-approve',
      request_id: 'req-1',
      tool_name: 'bash',
      params: { cmd: 'ls' },
      reason: 'needs dir',
      status: 'pending',
      message: '',
    }
    chatStore.setApprovalEvent(ev)

    await service.approve('req-1', chatStore, 'go ahead')

    expect(approvalApi.lastRequestId).toBe('req-1')
    expect(approvalApi.lastChatId).toBe('chat-approve')
    expect(approvalApi.lastApproved).toBe(true)
    expect(approvalApi.lastReason).toBe('go ahead')
    expect(chatStore.approvalEvent.value!.status).toBe('approved')
    expect(chatStore.agentPhase.value).toBe('thinking')
  })

  it('approval requires a matching approval event request id', async () => {
    chatStore.chatId.value = 'active-chat'
    chatStore.setApprovalEvent({
      chat_id: 'approval-chat',
      request_id: 'req-expected',
      tool_name: 'bash',
      params: {},
      reason: 'needs approval',
      status: 'pending',
      message: '',
    })

    await service.approve('req-other', chatStore, 'go')

    expect(approvalApi.lastRequestId).toBeNull()
    expect(chatStore.connectionError.value).toMatchObject({
      code: 'APPROVAL_FAILED',
      message: 'Approval event does not match request_id',
    })
  })

  it('ignores duplicate in-flight approval submissions for the same request', async () => {
    let resolveApproval!: () => void
    let calls = 0
    approvalApi.approve = async () => {
      calls += 1
      await new Promise<void>(resolve => { resolveApproval = resolve })
    }
    chatStore.setApprovalEvent({
      chat_id: 'chat-approve',
      request_id: 'req-1',
      tool_name: 'bash',
      params: {},
      reason: 'needs approval',
      status: 'pending',
      message: '',
    })

    const first = service.approve('req-1', chatStore, 'go')
    const second = service.approve('req-1', chatStore, 'go')
    resolveApproval()
    await Promise.all([first, second])

    expect(calls).toBe(1)
    expect(chatStore.connectionError.value).toBeNull()
    expect(chatStore.approvalEvent.value!.status).toBe('approved')
  })

  // --- 9. reject sends correct body, updates chatStore.approvalEvent ---
  it('reject sends correct body and updates approvalEvent', async () => {
    const ev: ApprovalEvent = {
      chat_id: 'chat-reject',
      request_id: 'req-2',
      tool_name: 'rm',
      params: { path: '/tmp/x' },
      reason: 'dangerous',
      status: 'pending',
      message: '',
    }
    chatStore.setApprovalEvent(ev)

    await service.reject('req-2', chatStore, 'too risky')

    expect(approvalApi.lastRequestId).toBe('req-2')
    expect(approvalApi.lastChatId).toBe('chat-reject')
    expect(approvalApi.lastApproved).toBe(false)
    expect(approvalApi.lastReason).toBe('too risky')
    expect(chatStore.approvalEvent.value!.status).toBe('rejected')
    expect(chatStore.agentPhase.value).toBe('thinking')
  })
})
