import { describe, it, expect } from 'vitest'
import type {
  MessageType,
  Message,
  ApprovalStatus,
  ExecutionStatus,
  ToolCallInfo,
  ChatSession,
  AgentPhase,
  ReasoningEntry,
  ApprovalEvent,
  ErrorInfo,
} from '@/domain/models'

describe('domain/models — type-level compilation', () => {
  it('MessageType is a union of 5 string literals', () => {
    const types: MessageType[] = ['user', 'assistant', 'tool_call', 'tool_result', 'system']
    expect(types).toHaveLength(5)
  })

  it('Message has required fields', () => {
    const msg: Message = {
      message_id: 'm1',
      chat_id: 'c1',
      timestamp: '2024-01-01T00:00:00Z',
      type: 'user',
      content: 'hello',
    }
    expect(msg.message_id).toBe('m1')
  })

  it('Message has optional reasoning_content and tool_calls', () => {
    const msg: Message = {
      message_id: 'm2',
      chat_id: 'c2',
      timestamp: '2024-01-01T00:00:00Z',
      type: 'assistant',
      content: '',
      reasoning_content: 'Let me think...',
      tool_calls: [
        { id: 'call_1', type: 'function', function: { name: 'get_cpu', arguments: '{}' } },
      ],
    }
    expect(msg.reasoning_content).toBe('Let me think...')
    expect(msg.tool_calls).toHaveLength(1)
    expect(msg.tool_calls![0]!.function.name).toBe('get_cpu')
  })

  it('ApprovalStatus is PENDING | APPROVED | REJECTED | EXPIRED', () => {
    const statuses: ApprovalStatus[] = ['PENDING', 'APPROVED', 'REJECTED', 'EXPIRED']
    expect(statuses).toHaveLength(4)
  })

  it('ExecutionStatus is PENDING_APPROVAL | RUNNING | SUCCEEDED | FAILED', () => {
    const statuses: ExecutionStatus[] = ['PENDING_APPROVAL', 'RUNNING', 'SUCCEEDED', 'FAILED']
    expect(statuses).toHaveLength(4)
  })

  it('ToolCallInfo has required fields', () => {
    const tc: ToolCallInfo = {
      call_id: 'tc-1',
      chat_id: 'c1',
      tool_name: 'read_file',
      is_read_only: true,
      execution_status: 'RUNNING',
      timestamp: '2024-01-01T00:00:00Z',
    }
    expect(tc.call_id).toBe('tc-1')
    expect(tc.tool_name).toBe('read_file')
    expect(tc.is_read_only).toBe(true)
  })

  it('ToolCallInfo optional fields', () => {
    const tc: ToolCallInfo = {
      call_id: 'tc-2',
      chat_id: 'c2',
      tool_name: 'write_file',
      is_read_only: false,
      execution_status: 'SUCCEEDED',
      timestamp: '',
      server: 'filesystem',
      is_rollbackable: true,
      params: { path: '/tmp/test' },
      request_id: 'req-1',
      approval_status: 'APPROVED',
      execution_time_ms: 150,
      output: { bytes_written: 42 },
      error: { code: 500, message: 'oops', data: 'trace' },
    }
    expect(tc.server).toBe('filesystem')
    expect(tc.is_rollbackable).toBe(true)
    expect(tc.params).toEqual({ path: '/tmp/test' })
    expect(tc.request_id).toBe('req-1')
    expect(tc.approval_status).toBe('APPROVED')
    expect(tc.execution_time_ms).toBe(150)
    expect(tc.output).toEqual({ bytes_written: 42 })
    expect(tc.error).toEqual({ code: 500, message: 'oops', data: 'trace' })
  })

  it('ChatSession has required fields', () => {
    const session: ChatSession = {
      chat_id: 'c1',
      messages: [],
      executed_tool_list: [],
      timestamp: '2024-01-01T00:00:00Z',
    }
    expect(session.chat_id).toBe('c1')
    expect(session.messages).toEqual([])
  })

  it('ChatSession optional title', () => {
    const session: ChatSession = {
      chat_id: 'c1',
      title: 'My Chat',
      messages: [],
      executed_tool_list: [],
      timestamp: '',
    }
    expect(session.title).toBe('My Chat')
  })

  it('AgentPhase is a union of 7 string literals', () => {
    const phases: AgentPhase[] = [
      'idle', 'thinking', 'calling_tool', 'waiting_for_tool',
      'awaiting_approval', 'responding', 'done',
    ]
    expect(phases).toHaveLength(7)
  })

  it('ReasoningEntry has required fields', () => {
    const entry: ReasoningEntry = {
      message_id: 'r1',
      content: 'thinking...',
      done: false,
      timestamp: '2024-01-01T00:00:00Z',
    }
    expect(entry.content).toBe('thinking...')
    expect(entry.done).toBe(false)
  })

  it('ApprovalEvent has required fields', () => {
    const event: ApprovalEvent = {
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs deletion',
      status: 'pending',
      message: '',
    }
    expect(event.status).toBe('pending')
    // status can be approved or rejected too
    const approved: ApprovalEvent = { ...event, status: 'approved' }
    const rejected: ApprovalEvent = { ...event, status: 'rejected' }
    expect(approved.status).toBe('approved')
    expect(rejected.status).toBe('rejected')
  })

  it('ErrorInfo has code and message', () => {
    const err: ErrorInfo = { code: 'NETWORK_ERROR', message: 'Connection lost' }
    expect(err.code).toBe('NETWORK_ERROR')
    expect(err.message).toBe('Connection lost')
  })
})
