import type { ChatSession } from '@/domain/models'
import type { ChatStreamEvent } from '@/domain/sse-events'

export interface SseClient {
  connect(
    body: Record<string, unknown>,
    onEvent: (event: ChatStreamEvent) => void,
    signal: AbortSignal,
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
