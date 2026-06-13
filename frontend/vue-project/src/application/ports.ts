import type { ChatSession } from '@/domain/models'
import type { SSECallbacks } from '@/domain/sse-events'

export interface SseClient {
  connect(
    body: Record<string, unknown>,
    callbacks: SSECallbacks,
    signal: AbortSignal,
    onSessionId?: (chatId: string) => void
  ): Promise<void>
}

export interface SessionApi {
  listSessions(): Promise<ChatSession[]>
  getSession(chatId: string): Promise<ChatSession>
  deleteSession(chatId: string): Promise<void>
}

export interface ApprovalApi {
  approve(requestId: string, reason?: string): Promise<void>
  reject(requestId: string, reason?: string): Promise<void>
}
