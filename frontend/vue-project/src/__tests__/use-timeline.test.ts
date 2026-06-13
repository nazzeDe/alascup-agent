import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { useTimeline, type TimelineItem } from '@/presentation/composables/use-timeline'
import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent } from '@/domain/models'

function makeMsg(overrides: Partial<Message> = {}): Message {
  return {
    message_id: 'msg-1',
    chat_id: 'chat-1',
    timestamp: '2025-01-01T00:00:00.000Z',
    type: 'user',
    content: 'hello',
    ...overrides,
  }
}

function makeTc(overrides: Partial<ToolCallInfo> = {}): ToolCallInfo {
  return {
    call_id: 'tc-1',
    chat_id: 'chat-1',
    tool_name: 'read_file',
    is_read_only: true,
    execution_status: 'SUCCEEDED',
    timestamp: '2025-01-01T00:00:01.000Z',
    ...overrides,
  }
}

function makeReasoning(overrides: Partial<ReasoningEntry> = {}): ReasoningEntry {
  return {
    message_id: 'r-1',
    content: 'thinking...',
    done: false,
    timestamp: '2025-01-01T00:00:00.500Z',
    ...overrides,
  }
}

function makeApproval(overrides: Partial<ApprovalEvent> = {}): ApprovalEvent {
  return {
    request_id: 'req-1',
    tool_name: 'run_command',
    params: {},
    reason: 'needs approval',
    status: 'pending',
    message: '',
    ...overrides,
  }
}

describe('useTimeline', () => {
  it('returns empty array for empty inputs', () => {
    const timeline = useTimeline(
      ref([]),
      ref(new Map()),
      ref([]),
      ref(null),
    )
    expect(timeline.value).toEqual([])
  })

  it('creates one item per message when only messages present', () => {
    const msgs = ref<Message[]>([
      makeMsg({ message_id: 'm1', timestamp: '2025-01-01T00:00:00Z', content: 'first' }),
      makeMsg({ message_id: 'm2', timestamp: '2025-01-01T00:00:02Z', content: 'second' }),
    ])
    const timeline = useTimeline(msgs, ref(new Map()), ref([]), ref(null))
    expect(timeline.value).toHaveLength(2)
    expect(timeline.value[0]!.type).toBe('message')
    expect(timeline.value[0]!.data).toBe(msgs.value[0])
    expect(timeline.value[1]!.type).toBe('message')
    expect(timeline.value[1]!.data).toBe(msgs.value[1])
  })

  it('sorts all items by timestamp ascending', () => {
    const msgs = ref<Message[]>([
      makeMsg({ message_id: 'm1', timestamp: '2025-01-01T00:00:10Z' }),
    ])
    const tcs = ref(new Map<string, ToolCallInfo>([
      ['tc-early', makeTc({ call_id: 'tc-early', timestamp: '2025-01-01T00:00:05Z' })],
    ]))
    const reasonings = ref<ReasoningEntry[]>([
      makeReasoning({ message_id: 'r1', timestamp: '2025-01-01T00:00:01Z' }),
    ])

    const timeline = useTimeline(msgs, tcs, reasonings, ref(null))
    expect(timeline.value).toHaveLength(3)
    expect(timeline.value[0]!.type).toBe('reasoning')
    expect(timeline.value[1]!.type).toBe('tool_call')
    expect(timeline.value[2]!.type).toBe('message')
  })

  it('includes all tool calls from map', () => {
    const tcs = ref(new Map<string, ToolCallInfo>([
      ['a', makeTc({ call_id: 'a', tool_name: 'tool-a', timestamp: '2025-01-01T00:00:01Z' })],
      ['b', makeTc({ call_id: 'b', tool_name: 'tool-b', timestamp: '2025-01-01T00:00:02Z' })],
      ['c', makeTc({ call_id: 'c', tool_name: 'tool-c', timestamp: '2025-01-01T00:00:03Z' })],
    ]))

    const timeline = useTimeline(ref([]), tcs, ref([]), ref(null))
    expect(timeline.value).toHaveLength(3)
    expect(timeline.value.map(i => (i.data as ToolCallInfo).tool_name)).toEqual(['tool-a', 'tool-b', 'tool-c'])
  })

  it('includes approval event when not null', () => {
    const approval = ref(makeApproval())
    const timeline = useTimeline(ref([]), ref(new Map()), ref([]), approval)
    expect(timeline.value).toHaveLength(1)
    expect(timeline.value[0]!.type).toBe('approval')
    expect(timeline.value[0]!.data).toBe(approval.value)
  })

  it('excludes approval event when null', () => {
    const timeline = useTimeline(ref([]), ref(new Map()), ref([]), ref(null))
    expect(timeline.value).toHaveLength(0)
  })

  it('includes reasoning entries', () => {
    const reasonings = ref<ReasoningEntry[]>([
      makeReasoning({ message_id: 'r1', content: 'step 1', timestamp: '2025-01-01T00:00:01Z' }),
      makeReasoning({ message_id: 'r2', content: 'step 2', timestamp: '2025-01-01T00:00:02Z' }),
    ])
    const timeline = useTimeline(ref([]), ref(new Map()), reasonings, ref(null))
    expect(timeline.value).toHaveLength(2)
    expect(timeline.value.map(i => (i.data as ReasoningEntry).content)).toEqual(['step 1', 'step 2'])
  })

  it('handles mixed content with correct types and order', () => {
    // Message at t=3, tool call at t=1, reasoning at t=2, approval at "now"
    const msgs = ref<Message[]>([
      makeMsg({ message_id: 'm-late', timestamp: '2025-01-01T00:00:03Z', content: 'response' }),
    ])
    const tcs = ref(new Map<string, ToolCallInfo>([
      ['tc', makeTc({ call_id: 'tc', timestamp: '2025-01-01T00:00:01Z' })],
    ]))
    const reasonings = ref<ReasoningEntry[]>([
      makeReasoning({ message_id: 'r', timestamp: '2025-01-01T00:00:02Z' }),
    ])
    const approval = ref(makeApproval())

    const timeline = useTimeline(msgs, tcs, reasonings, approval)

    // Approval uses Date.now() so it will sort last, after the t=3 message
    expect(timeline.value).toHaveLength(4)
    expect(timeline.value[0]!.type).toBe('tool_call')
    expect(timeline.value[1]!.type).toBe('reasoning')
    expect(timeline.value[2]!.type).toBe('message')
    expect(timeline.value[3]!.type).toBe('approval')
  })

  it('reacts to ref changes', () => {
    const msgs = ref<Message[]>([])
    const timeline = useTimeline(msgs, ref(new Map()), ref([]), ref(null))
    expect(timeline.value).toHaveLength(0)

    msgs.value = [makeMsg({ message_id: 'm1', timestamp: '2025-01-01T00:00:00Z' })]
    expect(timeline.value).toHaveLength(1)
    expect(timeline.value[0]!.type).toBe('message')
  })

  it('sorts reasoning before message when timestamps are equal', () => {
    const ts = '2025-01-01T00:00:00Z'
    const msgs = ref<Message[]>([
      makeMsg({ message_id: 'm1', timestamp: ts, type: 'assistant', content: 'CPU is fine.' }),
    ])
    const reasonings = ref<ReasoningEntry[]>([
      makeReasoning({ message_id: 'm1', timestamp: ts, content: 'Let me think...' }),
    ])

    const timeline = useTimeline(msgs, ref(new Map()), reasonings, ref(null))
    expect(timeline.value).toHaveLength(2)
    expect(timeline.value[0]!.type).toBe('reasoning')
    expect(timeline.value[1]!.type).toBe('message')
  })
})
