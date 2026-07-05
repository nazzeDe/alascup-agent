import { describe, expect, it } from 'vitest'
import { projectTimeline } from '@/application/timeline-projection'
import type { ApprovalEvent, Message, ReasoningEntry, ToolCallInfo } from '@/domain/models'

describe('projectTimeline', () => {
  it('orders realtime chat state without reading current time', () => {
    const message: Message = {
      message_id: 'm1',
      chat_id: 'chat-1',
      timestamp: '2026-06-17T00:00:03.000Z',
      type: 'assistant',
      content: 'done',
    }
    const tool: ToolCallInfo = {
      call_id: 'tc-1',
      chat_id: 'chat-1',
      tool_name: 'bash',
      is_read_only: false,
      execution_status: 'RUNNING',
      timestamp: '2026-06-17T00:00:02.000Z',
    }
    const reasoning: ReasoningEntry = {
      message_id: 'r1',
      content: 'thinking',
      done: true,
      timestamp: '2026-06-17T00:00:01.000Z',
    }
    const approval: ApprovalEvent = {
      chat_id: 'chat-1',
      request_id: 'req-1',
      tool_name: 'bash',
      params: {},
      reason: 'needs approval',
      status: 'pending',
      message: '',
      timestamp: '2026-06-17T00:00:02.500Z',
    }

    const timeline = projectTimeline({
      messages: [message],
      toolCalls: [tool],
      reasonings: [reasoning],
      approvalEvent: approval,
    })

    expect(timeline.map(item => item.type)).toEqual([
      'reasoning',
      'tool_call',
      'approval',
      'message',
    ])
  })
})
