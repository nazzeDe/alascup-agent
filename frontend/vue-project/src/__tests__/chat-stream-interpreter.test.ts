import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ChatStreamInterpreter } from '@/application/chat-stream-interpreter'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { ToastStore } from '@/application/toast-store'
import type { ActiveSessionWorkspace } from '@/application/active-session-workspace'

describe('ChatStreamInterpreter', () => {
  let chatStore: ChatStore
  let sessionListStore: SessionListStore
  let toastStore: ToastStore
  let nextId: number

  function makeInterpreter(isNewChat = false) {
    const activeSessionWorkspace: Pick<ActiveSessionWorkspace, 'adoptServerSession'> = {
      adoptServerSession(chatId: string, title: string) {
        chatStore.chatId.value = chatId
        sessionListStore.addSession({
          chat_id: chatId,
          title,
          messages: [],
          executed_tool_list: [],
          timestamp: '2026-06-17T00:00:00.000Z',
        })
        sessionListStore.setActive(chatId)
      },
    }
    return new ChatStreamInterpreter({
      chatStore,
      activeSessionWorkspace,
      toastStore,
      isNewChat,
      firstMessageTitle: 'hello',
      idFactory: () => `id-${++nextId}`,
      now: () => '2026-06-17T00:00:00.000Z',
      toolWaitMs: 800,
    })
  }

  beforeEach(() => {
    chatStore = new ChatStore()
    sessionListStore = new SessionListStore()
    toastStore = new ToastStore()
    nextId = 0
  })

  it('adopts session_init for new chats exactly once', () => {
    const interpreter = makeInterpreter(true)
    interpreter.apply({ type: 'session_init', data: { chat_id: 'chat-1' } })

    expect(chatStore.chatId.value).toBe('chat-1')
    expect(sessionListStore.activeChatId.value).toBe('chat-1')
    expect(sessionListStore.sessions.value).toHaveLength(1)
    expect(sessionListStore.sessions.value[0]!.title).toBe('hello')
  })

  it('accumulates reasoning and marks it done before assistant output', () => {
    const interpreter = makeInterpreter()
    interpreter.apply({ type: 'reasoning', data: { delta: 'I need' } })
    interpreter.apply({ type: 'reasoning', data: { delta: ' context' } })
    interpreter.apply({ type: 'assistant', data: { delta: 'Answer' } })

    expect(chatStore.reasonings.value).toHaveLength(1)
    expect(chatStore.reasonings.value[0]).toMatchObject({
      message_id: 'id-1',
      content: 'I need context',
      done: true,
    })
    expect(chatStore.agentPhase.value).toBe('responding')
  })

  it('streams assistant deltas into the active assistant message', () => {
    chatStore.chatId.value = 'chat-1'
    const interpreter = makeInterpreter()
    interpreter.apply({ type: 'session_init', data: { chat_id: 'chat-42' } })
    interpreter.apply({ type: 'assistant', data: { delta: 'Hel' } })

    expect(chatStore.messages.value.filter(m => m.type === 'assistant')).toHaveLength(1)
    expect(chatStore.messages.value.find(m => m.type === 'assistant')).toMatchObject({
      message_id: 'id-1',
      chat_id: 'chat-42',
      content: 'Hel',
    })

    interpreter.apply({ type: 'assistant', data: { delta: 'lo' } })
    interpreter.apply({ type: 'assistant_done', data: {} })

    const assistant = chatStore.messages.value.find(m => m.type === 'assistant')
    expect(assistant).toMatchObject({
      message_id: 'id-1',
      chat_id: 'chat-42',
      content: 'Hello',
    })
  })

  it('updates tool execution state through call, approval, and result', () => {
    const interpreter = makeInterpreter()
    interpreter.apply({
      type: 'tool_call',
      data: { call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'ls' }, is_read_only: false },
    })
    expect(chatStore.toolCalls.value.get('tc-1')!.execution_status).toBe('RUNNING')

    interpreter.apply({
      type: 'tool_approval_required',
      data: {
        chat_id: 'chat-1',
        request_id: 'req-1',
        tool_name: 'bash',
        params: { cmd: 'ls' },
        reason: 'needs approval',
        call_id: 'tc-1',
      },
    })
    expect(chatStore.agentPhase.value).toBe('awaiting_approval')
    expect(chatStore.toolCalls.value.get('tc-1')!.execution_status).toBe('PENDING_APPROVAL')

    interpreter.apply({
      type: 'tool_result',
      data: { call_id: 'tc-1', execution_status: 'SUCCEEDED', output: { ok: true } },
    })
    expect(chatStore.agentPhase.value).toBe('thinking')
    expect(chatStore.toolCalls.value.get('tc-1')!.execution_status).toBe('SUCCEEDED')
  })

  it('accepts rejected tool results from approval rejection flow', () => {
    const interpreter = makeInterpreter()
    interpreter.apply({
      type: 'tool_call',
      data: { call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'rm' }, is_read_only: false },
    })
    interpreter.apply({
      type: 'tool_result',
      data: {
        call_id: 'tc-1',
        execution_status: 'REJECTED',
        error: { message: 'Tool was rejected by human. Do NOT retry.' },
      },
    })

    expect(chatStore.toolCalls.value.get('tc-1')).toMatchObject({
      execution_status: 'REJECTED',
      error: { message: 'Tool was rejected by human. Do NOT retry.' },
    })
  })

  it('creates a pending tool call when approval arrives before tool_call', () => {
    const interpreter = makeInterpreter()

    interpreter.apply({
      type: 'tool_approval_required',
      data: {
        chat_id: 'chat-1',
        request_id: 'req-1',
        tool_name: 'bash',
        params: { cmd: 'ls' },
        reason: 'needs approval',
        call_id: 'tc-1',
      },
    })

    expect(chatStore.toolCalls.value.get('tc-1')).toMatchObject({
      call_id: 'tc-1',
      tool_name: 'bash',
      execution_status: 'PENDING_APPROVAL',
    })
  })

  it('does not downgrade a pending approval tool call on duplicate tool_call', () => {
    const interpreter = makeInterpreter()

    interpreter.apply({
      type: 'tool_call',
      data: { call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'ls' }, is_read_only: false },
    })
    interpreter.apply({
      type: 'tool_approval_required',
      data: {
        chat_id: 'chat-1',
        request_id: 'req-1',
        tool_name: 'bash',
        params: { cmd: 'ls' },
        reason: 'needs approval',
        call_id: 'tc-1',
      },
    })
    interpreter.apply({
      type: 'tool_call',
      data: { call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'ls' }, is_read_only: false },
    })

    expect(chatStore.toolCalls.value.get('tc-1')!.execution_status).toBe('PENDING_APPROVAL')
  })

  it('moves slow tool calls to waiting_for_tool', async () => {
    vi.useFakeTimers()
    const interpreter = makeInterpreter()
    interpreter.apply({
      type: 'tool_call',
      data: { call_id: 'tc-1', tool_name: 'slow', params: {}, is_read_only: true },
    })

    await vi.advanceTimersByTimeAsync(801)
    expect(chatStore.agentPhase.value).toBe('waiting_for_tool')
    vi.useRealTimers()
  })

  it('sets connection error on stream error', () => {
    const interpreter = makeInterpreter()
    interpreter.apply({ type: 'error', data: { code: 'AGENT_CRASH', message: 'boom' } })

    expect(chatStore.isStreaming.value).toBe(false)
    expect(chatStore.agentPhase.value).toBe('idle')
    expect(chatStore.connectionError.value).toEqual({ code: 'AGENT_CRASH', message: 'boom' })
  })
})
