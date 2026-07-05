import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FetchApprovalApi } from '@/infrastructure/approval-api'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('infrastructure/approval-api — FetchApprovalApi', () => {
  let api: FetchApprovalApi

  beforeEach(() => {
    mockFetch.mockReset()
    api = new FetchApprovalApi()
  })

  it('approve sends POST with APPROVED status and reason', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    await api.approve('req-1', 'chat-1', 'looks safe')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-1/approval',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: 'chat-1', approval_status: 'APPROVED', reason: 'looks safe' }),
      },
    )
  })

  it('approve defaults reason to empty string', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    await api.approve('req-2', 'chat-2')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-2/approval',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: 'chat-2', approval_status: 'APPROVED', reason: '' }),
      },
    )
  })

  it('approve throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
    })

    await expect(api.approve('req-1', 'chat-1')).rejects.toThrow('HTTP 500')
  })

  it('reject sends POST with REJECTED status and reason', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    await api.reject('req-1', 'chat-1', 'not safe')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-1/approval',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: 'chat-1', approval_status: 'REJECTED', reason: 'not safe' }),
      },
    )
  })

  it('reject defaults reason to empty string', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    await api.reject('req-2', 'chat-2')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/tool-requests/req-2/approval',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: 'chat-2', approval_status: 'REJECTED', reason: '' }),
      },
    )
  })

  it('reject throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
    })

    await expect(api.reject('req-1', 'chat-1')).rejects.toThrow('HTTP 400')
  })

  it('reject throws on network error', async () => {
    mockFetch.mockRejectedValue(new Error('Network down'))

    await expect(api.reject('req-1', 'chat-1')).rejects.toThrow('Network down')
  })
})
