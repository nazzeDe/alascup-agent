import type { ApprovalEvent, Message, ReasoningEntry, ToolCallInfo } from '@/domain/models'

export type TimelineItem =
  | { type: 'message'; data: Message; ts: number }
  | { type: 'tool_call'; data: ToolCallInfo; ts: number }
  | { type: 'reasoning'; data: ReasoningEntry; ts: number }
  | { type: 'approval'; data: ApprovalEvent; ts: number }

const TYPE_ORDER: Record<TimelineItem['type'], number> = {
  reasoning: 0,
  message: 1,
  tool_call: 2,
  approval: 3,
}

function timestampValue(timestamp?: string): number {
  if (!timestamp) return 0
  const value = new Date(timestamp).getTime()
  return Number.isNaN(value) ? 0 : value
}

export function projectTimeline(input: {
  messages: Message[]
  toolCalls: Iterable<ToolCallInfo>
  reasonings: ReasoningEntry[]
  approvalEvent: ApprovalEvent | null
}): TimelineItem[] {
  const items: TimelineItem[] = []
  for (const m of input.messages) {
    items.push({ type: 'message', data: m, ts: timestampValue(m.timestamp) })
  }
  for (const tc of input.toolCalls) {
    items.push({ type: 'tool_call', data: tc, ts: timestampValue(tc.timestamp) })
  }
  for (const r of input.reasonings) {
    items.push({ type: 'reasoning', data: r, ts: timestampValue(r.timestamp) })
  }
  if (input.approvalEvent) {
    items.push({
      type: 'approval',
      data: input.approvalEvent,
      ts: timestampValue(input.approvalEvent.timestamp),
    })
  }
  return items.sort((a, b) => a.ts - b.ts || TYPE_ORDER[a.type] - TYPE_ORDER[b.type])
}
