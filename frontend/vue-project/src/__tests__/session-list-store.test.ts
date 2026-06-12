import { describe, it, expect, beforeEach } from 'vitest'
import { SessionListStore } from '@/application/session-list-store'
import type { ChatSession } from '@/domain/models'

function makeSession(chatId: string, title?: string): ChatSession {
  return {
    chat_id: chatId,
    title,
    messages: [],
    executed_tool_list: [],
    timestamp: new Date().toISOString(),
  }
}

describe('SessionListStore', () => {
  let store: SessionListStore

  beforeEach(() => {
    store = new SessionListStore()
  })

  it('initializes with empty state', () => {
    expect(store.sessions.value).toEqual([])
    expect(store.activeChatId.value).toBeNull()
    expect(store.isLoadingSessions.value).toBe(false)
    expect(store.loadError.value).toBe('')
  })

  it('setSessions replaces the session list', () => {
    const sessions = [makeSession('c1', 'Chat 1'), makeSession('c2', 'Chat 2')]
    store.setSessions(sessions)
    expect(store.sessions.value).toHaveLength(2)
    expect(store.sessions.value[0]!.chat_id).toBe('c1')
    expect(store.sessions.value[1]!.chat_id).toBe('c2')
  })

  it('addSession prepends a session', () => {
    store.setSessions([makeSession('c1')])
    store.addSession(makeSession('c2'))
    expect(store.sessions.value).toHaveLength(2)
    expect(store.sessions.value[0]!.chat_id).toBe('c2')
  })

  it('removeSession filters by chatId', () => {
    store.setSessions([makeSession('c1'), makeSession('c2'), makeSession('c3')])
    store.removeSession('c2')
    expect(store.sessions.value).toHaveLength(2)
    expect(store.sessions.value.map(s => s.chat_id)).toEqual(['c1', 'c3'])
  })

  it('removeSession handles unknown chatId gracefully', () => {
    store.setSessions([makeSession('c1')])
    store.removeSession('unknown')
    expect(store.sessions.value).toHaveLength(1)
  })

  it('setActive sets activeChatId', () => {
    store.setActive('c1')
    expect(store.activeChatId.value).toBe('c1')
  })

  it('setActive null clears activeChatId', () => {
    store.setActive('c1')
    store.setActive(null)
    expect(store.activeChatId.value).toBeNull()
  })

  it('setLoading toggles loading state', () => {
    store.setLoading(true)
    expect(store.isLoadingSessions.value).toBe(true)
    store.setLoading(false)
    expect(store.isLoadingSessions.value).toBe(false)
  })

  it('setError sets error message', () => {
    store.setError('Failed to load')
    expect(store.loadError.value).toBe('Failed to load')
    store.setError('')
    expect(store.loadError.value).toBe('')
  })
})
