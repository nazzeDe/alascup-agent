import type { SessionApi, ApprovalApi } from '@/application/ports'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import type { ToolCallInfo } from '@/domain/models'

export class SessionService {
  constructor(
    private sessionApi: SessionApi,
    private approvalApi: ApprovalApi,
    private sessionListStore: SessionListStore,
  ) {}

  async createDraft(): Promise<string | null> {
    try {
      const result = await this.sessionApi.createSession()
      const chatId = result.chat_id
      this.sessionListStore.setActive(chatId)
      this.sessionListStore.addSession({
        chat_id: chatId,
        messages: [],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      })
      return chatId
    } catch {
      return null
    }
  }

  async loadSessions(): Promise<void> {
    this.sessionListStore.setLoading(true)
    this.sessionListStore.setError('')
    try {
      const sessions = await this.sessionApi.listSessions()
      this.sessionListStore.setSessions(sessions)
    } catch {
      this.sessionListStore.setError('Cannot connect to server')
    } finally {
      this.sessionListStore.setLoading(false)
    }
  }

  async loadHistory(chatId: string | null, chatStore: ChatStore): Promise<boolean> {
    this.sessionListStore.setActive(chatId)
    chatStore.setLoadingHistory(true)
    chatStore.setApprovalEvent(null)
    chatStore.setPhase('idle')
    chatStore.setConnectionError(null)

    if (chatId === null) {
      chatStore.messages.value = []
      chatStore.setToolCalls(new Map())
      chatStore.clearReasonings()
      chatStore.setLoadingHistory(false)
      return true
    }

    try {
      const session = await this.sessionApi.getSession(chatId)
      chatStore.messages.value = session.messages ?? []
      const map = new Map<string, ToolCallInfo>()
      if (session.executed_tool_list) {
        for (const tc of session.executed_tool_list) {
          map.set(tc.call_id, tc)
        }
      }
      chatStore.setToolCalls(map)
      chatStore.clearReasonings()
      chatStore.setLoadingHistory(false)
      return true
    } catch {
      chatStore.setLoadingHistory(false)
      return false
    }
  }

  async deleteSession(chatId: string): Promise<void> {
    try {
      await this.sessionApi.deleteSession(chatId)
    } catch {
      // Best-effort: clean up locally even if backend unreachable
    }
    this.sessionListStore.removeSession(chatId)
    if (this.sessionListStore.activeChatId.value === chatId) {
      this.sessionListStore.setActive(null)
    }
  }

  async approve(requestId: string, chatStore: ChatStore, message?: string): Promise<void> {
    try {
      await this.approvalApi.approve(requestId, message)
      chatStore.updateApprovalStatus('approved', message)
      chatStore.setPhase('thinking')
    } catch (err: unknown) {
      chatStore.setConnectionError({
        code: 'APPROVAL_FAILED',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }

  async reject(requestId: string, chatStore: ChatStore, message?: string): Promise<void> {
    try {
      await this.approvalApi.reject(requestId, message)
      chatStore.updateApprovalStatus('rejected', message)
      chatStore.setPhase('thinking')
    } catch (err: unknown) {
      chatStore.setConnectionError({
        code: 'APPROVAL_FAILED',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }
}
