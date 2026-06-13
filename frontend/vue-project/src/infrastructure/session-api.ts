import type { SessionApi } from '@/application/ports'
import type { ChatSession } from '@/domain/models'

export class FetchSessionApi implements SessionApi {
  async listSessions(): Promise<ChatSession[]> {
    const res = await fetch('/api/sessions')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }

  async getSession(chatId: string): Promise<ChatSession> {
    const res = await fetch(`/api/sessions/${chatId}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }

  async deleteSession(chatId: string): Promise<void> {
    const res = await fetch(`/api/sessions/${chatId}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  }
}
