import { computed, type Ref, type ComputedRef } from 'vue'
import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent } from '@/domain/models'
import { projectTimeline, type TimelineItem } from '@/application/timeline-projection'
export type { TimelineItem } from '@/application/timeline-projection'

export function useTimeline(
  messages: Ref<Message[]>,
  toolCalls: Ref<Map<string, ToolCallInfo>>,
  reasonings: Ref<ReasoningEntry[]>,
  approvalEvent: Ref<ApprovalEvent | null>,
): ComputedRef<TimelineItem[]> {
  return computed(() => {
    return projectTimeline({
      messages: messages.value,
      toolCalls: toolCalls.value.values(),
      reasonings: reasonings.value,
      approvalEvent: approvalEvent.value,
    })
  })
}
