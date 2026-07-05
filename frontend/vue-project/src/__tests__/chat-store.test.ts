import { describe, it, expect, beforeEach } from 'vitest'
import { ChatStore } from '@/application/chat-store'
import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent, AgentPhase } from '@/domain/models'

describe('ChatStore', () => {
  let store: ChatStore

  beforeEach(() => {
    store = new ChatStore()
  })

  // --- Initial state ---
  it('initializes with empty state', () => {
    expect(store.messages.value).toEqual([])
    expect(store.toolCalls.value.size).toBe(0)
    expect(store.reasonings.value).toEqual([])
    expect(store.isStreaming.value).toBe(false)
    expect(store.draftInput.value).toBe('')
    expect(store.approvalEvent.value).toBeNull()
    expect(store.agentPhase.value).toBe('idle')
    expect(store.phaseLabel.value).toBe('')
    expect(store.isLoadingHistory.value).toBe(false)
    expect(store.connectionError.value).toBeNull()
  })

  // --- phaseLabel computed ---
  it.each([
    ['thinking', '正在思考…'],
    ['responding', '正在回复…'],
    ['calling_tool', '正在调用工具…'],
    ['waiting_for_tool', '等待工具调用结果'],
    ['awaiting_approval', '等待审批…'],
    ['done', ''],
    ['idle', ''],
  ] as [AgentPhase, string][])('phaseLabel for %s returns %s', (phase, expected) => {
    store.setPhase(phase)
    expect(store.phaseLabel.value).toBe(expected)
  })

  // --- Message mutations ---
  it('addMessage appends a message', () => {
    const msg: Message = {
      message_id: 'm1',
      chat_id: 'c1',
      timestamp: '2024-01-01T00:00:00Z',
      type: 'user',
      content: 'hello',
    }
    store.addMessage(msg)
    expect(store.messages.value).toHaveLength(1)
    expect(store.messages.value[0]!).toEqual(msg)

    const msg2: Message = { ...msg, message_id: 'm2', type: 'assistant', content: 'hi' }
    store.addMessage(msg2)
    expect(store.messages.value).toHaveLength(2)
  })

  it('addMessages appends multiple messages', () => {
    const msgs: Message[] = [
      { message_id: 'm1', chat_id: 'c1', timestamp: 't1', type: 'user', content: 'a' },
      { message_id: 'm2', chat_id: 'c1', timestamp: 't2', type: 'assistant', content: 'b' },
    ]
    store.addMessages(msgs)
    expect(store.messages.value).toHaveLength(2)
    expect(store.messages.value[0]!.message_id).toBe('m1')
    expect(store.messages.value[1]!.message_id).toBe('m2')
  })

  // --- ToolCall mutations ---
  it('setToolCall adds a new tool call', () => {
    const tc: ToolCallInfo = {
      call_id: 'c1',
      chat_id: 'chat1',
      tool_name: 'bash',
      is_read_only: false,
      execution_status: 'RUNNING',
      timestamp: 't1',
    }
    store.setToolCall('c1', tc)
    expect(store.toolCalls.value.size).toBe(1)
    expect(store.toolCalls.value.get('c1')).toEqual(tc)
  })

  it('setToolCall overwrites existing tool call', () => {
    const tc: ToolCallInfo = {
      call_id: 'c1',
      chat_id: 'chat1',
      tool_name: 'bash',
      is_read_only: false,
      execution_status: 'RUNNING',
      timestamp: 't1',
    }
    store.setToolCall('c1', tc)

    const updated: ToolCallInfo = { ...tc, execution_status: 'SUCCEEDED', output: {} }
    store.setToolCall('c1', updated)
    expect(store.toolCalls.value.get('c1')!.execution_status).toBe('SUCCEEDED')
  })

  it('updateToolCall updates partial fields', () => {
    const tc: ToolCallInfo = {
      call_id: 'c1',
      chat_id: 'chat1',
      tool_name: 'bash',
      is_read_only: false,
      execution_status: 'PENDING_APPROVAL',
      approval_status: 'PENDING',
      timestamp: 't1',
    }
    store.setToolCall('c1', tc)
    store.updateToolCall('c1', { execution_status: 'RUNNING' })
    const updated = store.toolCalls.value.get('c1')!
    expect(updated.execution_status).toBe('RUNNING')
    expect(updated.approval_status).toBe('PENDING') // preserved
    expect(updated.tool_name).toBe('bash') // preserved
  })

  it('updateToolCall is a no-op for unknown callId', () => {
    store.updateToolCall('unknown', { execution_status: 'FAILED' })
    expect(store.toolCalls.value.size).toBe(0)
  })

  it('setToolCalls replaces the entire map', () => {
    const map = new Map<string, ToolCallInfo>()
    const tc: ToolCallInfo = {
      call_id: 'c1',
      chat_id: 'chat1',
      tool_name: 'bash',
      is_read_only: false,
      execution_status: 'SUCCEEDED',
      timestamp: 't1',
    }
    map.set('c1', tc)
    store.setToolCalls(map)
    expect(store.toolCalls.value.get('c1')!.tool_name).toBe('bash')
  })

  // --- Reasoning mutations ---
  it('addReasoningEntry appends a reasoning entry', () => {
    const entry: ReasoningEntry = {
      message_id: 'r1',
      content: 'thinking...',
      done: false,
      timestamp: 't1',
    }
    store.addReasoningEntry(entry)
    expect(store.reasonings.value).toHaveLength(1)
    expect(store.reasonings.value[0]!.content).toBe('thinking...')
  })

  it('appendReasoningDelta appends to existing entry or creates new', () => {
    store.appendReasoningDelta('r1', 'hello')
    expect(store.reasonings.value).toHaveLength(1)
    expect(store.reasonings.value[0]!.content).toBe('hello')
    expect(store.reasonings.value[0]!.done).toBe(false)

    store.appendReasoningDelta('r1', ' world')
    expect(store.reasonings.value[0]!.content).toBe('hello world')
  })

  it('markReasoningDone sets done flag', () => {
    store.appendReasoningDelta('r1', 'done thinking')
    store.markReasoningDone('r1')
    expect(store.reasonings.value[0]!.done).toBe(true)
  })

  it('markReasoningDone is a no-op for unknown messageId', () => {
    store.markReasoningDone('nonexistent')
    expect(store.reasonings.value).toHaveLength(0)
  })

  it('clearReasonings empties reasonings', () => {
    store.appendReasoningDelta('r1', 'a')
    store.appendReasoningDelta('r2', 'b')
    store.clearReasonings()
    expect(store.reasonings.value).toEqual([])
  })

  // --- Simple state setters ---
  it('setPhase updates phase', () => {
    store.setPhase('calling_tool')
    expect(store.agentPhase.value).toBe('calling_tool')
  })

  it('setStreaming toggles streaming', () => {
    store.setStreaming(true)
    expect(store.isStreaming.value).toBe(true)
    store.setStreaming(false)
    expect(store.isStreaming.value).toBe(false)
  })

  it('setDraftInput updates draft', () => {
    store.setDraftInput('hello world')
    expect(store.draftInput.value).toBe('hello world')
  })

  it('setConnectionError sets and clears error', () => {
    store.setConnectionError({ code: 'ERR', message: 'boom' })
    expect(store.connectionError.value).toEqual({ code: 'ERR', message: 'boom' })
    store.setConnectionError(null)
    expect(store.connectionError.value).toBeNull()
  })

  it('setLoadingHistory toggles loading', () => {
    store.setLoadingHistory(true)
    expect(store.isLoadingHistory.value).toBe(true)
    store.setLoadingHistory(false)
    expect(store.isLoadingHistory.value).toBe(false)
  })

  // --- ApprovalEvent mutations ---
  it('setApprovalEvent sets and clears approval', () => {
    const ev: ApprovalEvent = {
      chat_id: 'chat-1',
      request_id: 'req1',
      tool_name: 'bash',
      params: { cmd: 'ls' },
      reason: 'needs dir list',
      status: 'pending',
      message: '',
    }
    store.setApprovalEvent(ev)
    expect(store.approvalEvent.value).toEqual(ev)

    store.setApprovalEvent(null)
    expect(store.approvalEvent.value).toBeNull()
  })

  it('updateApprovalStatus updates pending approval', () => {
    const ev: ApprovalEvent = {
      chat_id: 'chat-1',
      request_id: 'req1',
      tool_name: 'bash',
      params: { cmd: 'ls' },
      reason: 'needs dir list',
      status: 'pending',
      message: '',
    }
    store.setApprovalEvent(ev)
    store.updateApprovalStatus('approved', 'looks good')
    expect(store.approvalEvent.value!.status).toBe('approved')
    expect(store.approvalEvent.value!.message).toBe('looks good')
  })

  it('updateApprovalStatus is a no-op when no approval event', () => {
    store.updateApprovalStatus('rejected', 'nope')
    expect(store.approvalEvent.value).toBeNull()
  })

  // --- reset ---
  it('reset clears all state to initial', () => {
    store.addMessage({ message_id: 'm1', chat_id: 'c1', timestamp: 't', type: 'user', content: 'hi' })
    store.setPhase('thinking')
    store.setStreaming(true)
    store.setDraftInput('draft')
    store.setLoadingHistory(true)
    store.setConnectionError({ code: 'E', message: 'err' })

    store.reset()

    expect(store.messages.value).toEqual([])
    expect(store.toolCalls.value.size).toBe(0)
    expect(store.reasonings.value).toEqual([])
    expect(store.isStreaming.value).toBe(false)
    expect(store.draftInput.value).toBe('')
    expect(store.approvalEvent.value).toBeNull()
    expect(store.agentPhase.value).toBe('idle')
    expect(store.isLoadingHistory.value).toBe(false)
    expect(store.connectionError.value).toBeNull()
  })

  // --- loadFromSession ---
  it('loadFromSession restores messages and tool calls', () => {
    const msgs: Message[] = [
      { message_id: 'm1', chat_id: 'c1', timestamp: 't1', type: 'user', content: 'hi' },
      { message_id: 'm2', chat_id: 'c1', timestamp: 't2', type: 'assistant', content: 'hello' },
    ]
    const tcs: ToolCallInfo[] = [
      {
        call_id: 'tc1',
        chat_id: 'c1',
        tool_name: 'read',
        is_read_only: true,
        execution_status: 'SUCCEEDED',
        timestamp: 't3',
      },
    ]

    store.loadFromSession({ messages: msgs, executed_tool_list: tcs })

    expect(store.messages.value).toHaveLength(2)
    expect(store.messages.value[0]!.message_id).toBe('m1')
    expect(store.messages.value[1]!.message_id).toBe('m2')
    expect(store.toolCalls.value.size).toBe(1)
    expect(store.toolCalls.value.get('tc1')!.tool_name).toBe('read')
  })

  it('loadFromSession extracts reasoning_content from assistant messages', () => {
    const msgs: Message[] = [
      { message_id: 'm1', chat_id: 'c1', timestamp: 't1', type: 'user', content: 'check CPU' },
      {
        message_id: 'm2', chat_id: 'c1', timestamp: 't2', type: 'assistant', content: '',
        reasoning_content: 'Let me think about which tool to use...',
        tool_calls: [
          { id: 'call_1', type: 'function', function: { name: 'get_cpu', arguments: '{}' } },
        ],
      },
      { message_id: 'm3', chat_id: 'c1', timestamp: 't3', type: 'assistant', content: 'CPU is fine.' },
    ]
    const tcs: ToolCallInfo[] = [
      {
        call_id: 'call_1', chat_id: 'c1', tool_name: 'get_cpu',
        is_read_only: true, execution_status: 'SUCCEEDED', timestamp: 't2.5',
      },
    ]

    store.loadFromSession({ messages: msgs, executed_tool_list: tcs })

    // Messages restored
    expect(store.messages.value).toHaveLength(3)
    // Reasoning extracted from assistant message m2
    expect(store.reasonings.value).toHaveLength(1)
    expect(store.reasonings.value[0]!.message_id).toBe('m2')
    expect(store.reasonings.value[0]!.content).toBe('Let me think about which tool to use...')
    expect(store.reasonings.value[0]!.done).toBe(true)
    expect(store.reasonings.value[0]!.timestamp).toBe('t2')
  })

  it('loadFromSession clears previous reasonings before extracting', () => {
    store.addReasoningEntry({
      message_id: 'old', content: 'stale', done: true, timestamp: 't0',
    })

    const msgs: Message[] = [
      {
        message_id: 'm1', chat_id: 'c1', timestamp: 't1', type: 'assistant', content: 'ok',
        reasoning_content: 'hmm',
      },
    ]

    store.loadFromSession({ messages: msgs, executed_tool_list: [] })
    expect(store.reasonings.value).toHaveLength(1)
    expect(store.reasonings.value[0]!.message_id).toBe('m1')
  })
})
