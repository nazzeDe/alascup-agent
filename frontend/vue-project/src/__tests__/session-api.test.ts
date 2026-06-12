import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FetchSessionApi } from '@/infrastructure/session-api'
import type { ChatSession } from '@/domain/models'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('infrastructure/session-api — FetchSessionApi', () => {
  let api: FetchSessionApi

  beforeEach(() => {
    mockFetch.mockReset()
    api = new FetchSessionApi()
  })

  const sampleSession: ChatSession = {
    chat_id: 'abc-123',
    title: 'Test Session',
    messages: [],
    executed_tool_list: [],
    timestamp: '2024-01-01T00:00:00Z',
  }

  it('listSessions calls GET /api/sessions', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [sampleSession],
    })

    const result = await api.listSessions()

    expect(mockFetch).toHaveBeenCalledWith('/api/sessions')
    expect(result).toHaveLength(1)
    expect(result[0]!.chat_id).toBe('abc-123')
  })

  it('listSessions throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
    })

    await expect(api.listSessions()).rejects.toThrow('HTTP 500')
  })

  it('listSessions throws on network error', async () => {
    mockFetch.mockRejectedValue(new Error('Network down'))

    await expect(api.listSessions()).rejects.toThrow('Network down')
  })

  it('getSession calls GET /api/sessions/:chatId', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => sampleSession,
    })

    const result = await api.getSession('abc-123')

    expect(mockFetch).toHaveBeenCalledWith('/api/sessions/abc-123')
    expect(result.chat_id).toBe('abc-123')
  })

  it('getSession throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
    })

    await expect(api.getSession('missing')).rejects.toThrow('HTTP 404')
  })

  it('createSession calls POST /api/sessions', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ chat_id: 'new-session-id' }),
    })

    const result = await api.createSession()

    expect(mockFetch).toHaveBeenCalledWith('/api/sessions', { method: 'POST' })
    expect(result.chat_id).toBe('new-session-id')
  })

  it('createSession throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
    })

    await expect(api.createSession()).rejects.toThrow('HTTP 500')
  })

  it('deleteSession calls DELETE /api/sessions/:chatId', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    await api.deleteSession('abc-123')

    expect(mockFetch).toHaveBeenCalledWith('/api/sessions/abc-123', {
      method: 'DELETE',
    })
  })

  it('deleteSession throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
    })

    await expect(api.deleteSession('abc-123')).rejects.toThrow('HTTP 500')
  })
})
