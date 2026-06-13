import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent, AgentPhase, ErrorInfo } from '@/domain/models'
import { ref, computed, type Ref, type ComputedRef } from 'vue'

export class ChatStore {
  readonly chatId: Ref<string | null> = ref(null)
  readonly messages: Ref<Message[]> = ref([])
  readonly toolCalls: Ref<Map<string, ToolCallInfo>> = ref(new Map())
  readonly reasonings: Ref<ReasoningEntry[]> = ref([])
  readonly isStreaming: Ref<boolean> = ref(false)
  readonly draftInput: Ref<string> = ref('')
  readonly approvalEvent: Ref<ApprovalEvent | null> = ref(null)
  readonly agentPhase: Ref<AgentPhase> = ref('idle')
  readonly isLoadingHistory: Ref<boolean> = ref(false)
  readonly connectionError: Ref<ErrorInfo | null> = ref(null)

  readonly phaseLabel: ComputedRef<string> = computed(() => {
    switch (this.agentPhase.value) {
      case 'thinking': return '正在思考…'
      case 'responding': return '正在回复…'
      case 'calling_tool': return '正在调用工具…'
      case 'waiting_for_tool': return '等待工具调用结果'
      case 'awaiting_approval': return '等待审批…'
      case 'done':
      case 'idle':
      default: return ''
    }
  })

  // Message mutations
  addMessage(msg: Message): void {
    this.messages.value = [...this.messages.value, msg]
  }

  addMessages(msgs: Message[]): void {
    this.messages.value = [...this.messages.value, ...msgs]
  }

  // ToolCall mutations
  setToolCall(callId: string, tc: ToolCallInfo): void {
    const updated = new Map(this.toolCalls.value)
    updated.set(callId, tc)
    this.toolCalls.value = updated
  }

  updateToolCall(callId: string, updates: Partial<ToolCallInfo>): void {
    const existing = this.toolCalls.value.get(callId)
    if (!existing) return
    const updated = new Map(this.toolCalls.value)
    updated.set(callId, { ...existing, ...updates })
    this.toolCalls.value = updated
  }

  setToolCalls(tcs: Map<string, ToolCallInfo>): void {
    this.toolCalls.value = tcs
  }

  // Reasoning mutations
  appendReasoningDelta(messageId: string, delta: string): void {
    const idx = this.reasonings.value.findIndex(r => r.message_id === messageId)
    if (idx !== -1) {
      const updated = [...this.reasonings.value]
      updated[idx] = { ...updated[idx]!, content: updated[idx]!.content + delta }
      this.reasonings.value = updated
    } else {
      this.reasonings.value = [...this.reasonings.value, {
        message_id: messageId,
        content: delta,
        done: false,
        timestamp: new Date().toISOString(),
      }]
    }
  }

  addReasoningEntry(entry: ReasoningEntry): void {
    this.reasonings.value = [...this.reasonings.value, entry]
  }

  markReasoningDone(messageId: string): void {
    this.reasonings.value = this.reasonings.value.map(r =>
      r.message_id === messageId ? { ...r, done: true } : r
    )
  }

  clearReasonings(): void {
    this.reasonings.value = []
  }

  // Simple state setters
  setPhase(phase: AgentPhase): void {
    this.agentPhase.value = phase
  }

  setStreaming(v: boolean): void {
    this.isStreaming.value = v
  }

  setDraftInput(text: string): void {
    this.draftInput.value = text
  }

  setConnectionError(err: ErrorInfo | null): void {
    this.connectionError.value = err
  }

  setLoadingHistory(v: boolean): void {
    this.isLoadingHistory.value = v
  }

  // ApprovalEvent mutations
  setApprovalEvent(ev: ApprovalEvent | null): void {
    this.approvalEvent.value = ev
  }

  updateApprovalStatus(status: 'approved' | 'rejected', message?: string): void {
    if (!this.approvalEvent.value) return
    this.approvalEvent.value = {
      ...this.approvalEvent.value,
      status,
      message: message ?? '',
    }
  }

  // Full reset
  reset(): void {
    this.chatId.value = null
    this.messages.value = []
    this.toolCalls.value = new Map()
    this.reasonings.value = []
    this.isStreaming.value = false
    this.draftInput.value = ''
    this.approvalEvent.value = null
    this.agentPhase.value = 'idle'
    this.isLoadingHistory.value = false
    this.connectionError.value = null
  }

  // Load from history
  loadFromSession(session: { messages: Message[]; executed_tool_list: ToolCallInfo[] }): void {
    this.messages.value = session.messages
    const map = new Map<string, ToolCallInfo>()
    for (const tc of session.executed_tool_list) {
      map.set(tc.call_id, tc)
    }
    this.toolCalls.value = map
  }
}
