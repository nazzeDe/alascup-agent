import { computed, type Ref, type ComputedRef } from 'vue'
import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent } from '@/domain/models'

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

export function useTimeline(
  messages: Ref<Message[]>,
  toolCalls: Ref<Map<string, ToolCallInfo>>,
  reasonings: Ref<ReasoningEntry[]>,
  approvalEvent: Ref<ApprovalEvent | null>,
): ComputedRef<TimelineItem[]> {
  return computed(() => {
    const items: TimelineItem[] = []
    for (const m of messages.value) {
      items.push({ type: 'message', data: m, ts: new Date(m.timestamp).getTime() })
    }
    toolCalls.value.forEach((tc) => {
      items.push({ type: 'tool_call', data: tc, ts: new Date(tc.timestamp).getTime() })
    })
    for (const r of reasonings.value) {
      items.push({ type: 'reasoning', data: r, ts: new Date(r.timestamp).getTime() })
    }
    if (approvalEvent.value) {
      items.push({ type: 'approval', data: approvalEvent.value, ts: Date.now() })
    }
    items.sort((a, b) => a.ts - b.ts || TYPE_ORDER[a.type] - TYPE_ORDER[b.type])
    return items
  })
}
