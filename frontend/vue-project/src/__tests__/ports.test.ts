import { describe, it, expect, vi } from 'vitest'
import type { SseClient, SessionApi, ApprovalApi } from '@/application/ports'
import type { ChatSession, ToolCallInfo, Message } from '@/domain/models'
import type { SSECallbacks } from '@/domain/sse-events'

describe('application/ports — interface structure verification', () => {
  // SseClient mock implementation
  class MockSseClient implements SseClient {
    async connect(
      body: Record<string, unknown>,
      callbacks: SSECallbacks,
      signal: AbortSignal,
      onSessionId?: (chatId: string) => void,
    ): Promise<void> {
      // Implementation for testing
      onSessionId?.('mock-chat-id')
    }
  }

  // SessionApi mock implementation
  class MockSessionApi implements SessionApi {
    async listSessions(): Promise<ChatSession[]> {
      return []
    }
    async getSession(chatId: string): Promise<ChatSession> {
      return {
        chat_id: chatId,
        messages: [],
        executed_tool_list: [],
        timestamp: '',
      }
    }
    async deleteSession(chatId: string): Promise<void> {
      // no-op
    }
  }

  // ApprovalApi mock implementation
  class MockApprovalApi implements ApprovalApi {
    async approve(requestId: string, reason?: string): Promise<void> {
      // no-op
    }
    async reject(requestId: string, reason?: string): Promise<void> {
      // no-op
    }
  }

  it('SseClient.connect accepts body, callbacks, signal, onSessionId', async () => {
    const client = new MockSseClient()
    const body = { chat_id: 'test', message: 'hello' }
    const callbacks: SSECallbacks = {
      on_assistant: () => {},
      on_done: () => {},
    }
    const ctrl = new AbortController()
    let capturedId = ''

    await client.connect(body, callbacks, ctrl.signal, (id) => {
      capturedId = id
    })

    expect(capturedId).toBe('mock-chat-id')
  })

  it('SessionApi.listSessions returns ChatSession array', async () => {
    const api = new MockSessionApi()
    const result = await api.listSessions()
    expect(Array.isArray(result)).toBe(true)
    expect(result).toHaveLength(0)
  })

  it('SessionApi.getSession returns a ChatSession by id', async () => {
    const api = new MockSessionApi()
    const result = await api.getSession('test-id')
    expect(result.chat_id).toBe('test-id')
    expect(result.messages).toEqual([])
    expect(result.executed_tool_list).toEqual([])
  })

  it('SessionApi.deleteSession resolves for a valid id', async () => {
    const api = new MockSessionApi()
    await expect(api.deleteSession('test-id')).resolves.toBeUndefined()
  })

  it('ApprovalApi.approve sends a request with optional reason', async () => {
    const api = new MockApprovalApi()
    await expect(api.approve('req-1', 'looks good')).resolves.toBeUndefined()
    await expect(api.approve('req-2')).resolves.toBeUndefined()
  })

  it('ApprovalApi.reject sends a request with optional reason', async () => {
    const api = new MockApprovalApi()
    await expect(api.reject('req-1', 'not safe')).resolves.toBeUndefined()
    await expect(api.reject('req-2')).resolves.toBeUndefined()
  })
})
