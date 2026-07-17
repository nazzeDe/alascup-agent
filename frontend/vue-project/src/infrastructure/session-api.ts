import type { SessionApi } from '@/application/ports'
import type { ChatSession } from '@/domain/models'
import { toChatSession, type SessionDto } from '@/infrastructure/session-dto-adapter'

export class FetchSessionApi implements SessionApi {
  async listSessions(): Promise<ChatSession[]> {
    const res = await fetch('/api/sessions')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const sessions = await res.json() as SessionDto[]
    return sessions.map(toChatSession)
  }

  async getSession(chatId: string): Promise<ChatSession> {
    const res = await fetch(`/api/sessions/${chatId}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const session = await res.json() as SessionDto
    return toChatSession(session)
  }

  async deleteSession(chatId: string): Promise<void> {
    const res = await fetch(`/api/sessions/${chatId}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  }
}
