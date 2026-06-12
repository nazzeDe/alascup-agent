import { describe, it, expect } from 'vitest'
import type {
  AssistantEvent,
  AssistantDoneEvent,
  ReasoningEvent,
  ThinkingDoneEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolApprovalRequiredEvent,
  SessionInitEvent,
  ErrorEvent,
  SSEEventType,
  SSECallbacks,
} from '@/domain/sse-events'

describe('domain/sse-events — wire format DTOs', () => {
  it('AssistantEvent has delta string', () => {
    const e: AssistantEvent = { delta: 'Hello' }
    expect(e.delta).toBe('Hello')
  })

  it('AssistantDoneEvent is an empty object', () => {
    const e: AssistantDoneEvent = {}
    expect(e).toEqual({})
  })

  it('ReasoningEvent has delta string', () => {
    const e: ReasoningEvent = { delta: 'thinking...' }
    expect(e.delta).toBe('thinking...')
  })

  it('ThinkingDoneEvent is an empty object', () => {
    const e: ThinkingDoneEvent = {}
    expect(e).toEqual({})
  })

  it('ToolCallEvent has call_id, tool_name, params, is_read_only, optional server', () => {
    const e: ToolCallEvent = {
      call_id: 'tc-1',
      tool_name: 'read_file',
      params: { path: '/tmp/test' },
      is_read_only: true,
      server: 'filesystem',
    }
    expect(e.call_id).toBe('tc-1')
    expect(e.tool_name).toBe('read_file')
    expect(e.params).toEqual({ path: '/tmp/test' })
    expect(e.is_read_only).toBe(true)
    expect(e.server).toBe('filesystem')
  })

  it('ToolCallEvent server is optional', () => {
    const e: ToolCallEvent = {
      call_id: 'tc-2',
      tool_name: 'echo',
      params: {},
      is_read_only: true,
    }
    expect(e.server).toBeUndefined()
  })

  it('ToolResultEvent has call_id and execution_status', () => {
    const e: ToolResultEvent = {
      call_id: 'tc-1',
      execution_status: 'SUCCEEDED',
      output: { content: 'hello' },
      execution_time_ms: 100,
    }
    expect(e.execution_status).toBe('SUCCEEDED')
    expect(e.output).toEqual({ content: 'hello' })
    expect(e.execution_time_ms).toBe(100)
  })

  it('ToolResultEvent can have error on FAILED', () => {
    const e: ToolResultEvent = {
      call_id: 'tc-2',
      execution_status: 'FAILED',
      error: { code: 500, message: 'boom' },
    }
    expect(e.error).toEqual({ code: 500, message: 'boom' })
  })

  it('ToolApprovalRequiredEvent has all fields', () => {
    const e: ToolApprovalRequiredEvent = {
      chat_id: 'c1',
      request_id: 'req-1',
      tool_name: 'rm',
      params: { path: '/etc/hosts' },
      reason: 'needs approval',
      call_id: 'tc-1',
    }
    expect(e.chat_id).toBe('c1')
    expect(e.request_id).toBe('req-1')
    expect(e.reason).toBe('needs approval')
    expect(e.call_id).toBe('tc-1')
  })

  it('SessionInitEvent has chat_id', () => {
    const e: SessionInitEvent = { chat_id: 'c1' }
    expect(e.chat_id).toBe('c1')
  })

  it('ErrorEvent has code and message', () => {
    const e: ErrorEvent = { code: 'AGENT_CRASH', message: 'boom' }
    expect(e.code).toBe('AGENT_CRASH')
    expect(e.message).toBe('boom')
  })

  it('SSEEventType is a union of 10 string literals', () => {
    const types: SSEEventType[] = [
      'assistant', 'assistant_done', 'reasoning', 'thinking_done',
      'tool_call', 'tool_result', 'tool_approval_required',
      'session_init', 'error', 'done',
    ]
    expect(types).toHaveLength(10)
  })

  it('SSECallbacks has optional callback for each event type', () => {
    const cbs: SSECallbacks = {
      on_assistant: (data) => { expect(typeof data.delta).toBe('string') },
      on_assistant_done: () => {},
      on_reasoning: (data) => { expect(typeof data.delta).toBe('string') },
      on_thinking_done: () => {},
      on_tool_call: (data) => { expect(typeof data.call_id).toBe('string') },
      on_tool_result: (data) => { expect(typeof data.call_id).toBe('string') },
      on_tool_approval_required: (data) => { expect(typeof data.request_id).toBe('string') },
      on_session_init: (data) => { expect(typeof data.chat_id).toBe('string') },
      on_error: (data) => { expect(typeof data.code).toBe('string') },
      on_done: () => {},
    }
    // All callbacks are optional — verify by creating a partial object
    const partial: SSECallbacks = { on_done: () => {} }
    expect(partial.on_done).toBeDefined()
    expect(partial.on_assistant).toBeUndefined()
  })
})
