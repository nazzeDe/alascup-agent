import { ref, computed, type Ref } from 'vue'
import type {
  Message,
  ToolCallInfo,
  AssistantEvent,
  ReasoningEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolApprovalRequiredEvent,
  ErrorEvent,
  DoneEvent,
  ChatSession,
} from '@/types'
import { useSSE } from './useSSE'
import { useToast } from './useToast'

export type AgentPhase =
  | 'idle'
  | 'thinking'
  | 'calling_tool'
  | 'awaiting_approval'
  | 'responding'
  | 'done'

export interface ReasoningEntry {
  message_id: string
  chat_id: string
  content: string
  done: boolean
  timestamp: string
}

export function useChat(options?: { onChatCreated?: (chatId: string) => void }) {
  const { connect, abort: sseAbort, isStreaming } = useSSE()
  const { showToast } = useToast()

  function abort(): void {
    sseAbort()
    approvalPending.value = null
    currentActivity.value = ''
    agentPhase.value = 'idle'
    reasonings.value = reasonings.value.map(r => ({ ...r, done: true }))
  }

  const messages: Ref<Message[]> = ref([])
  const toolCalls: Ref<Map<string, ToolCallInfo>> = ref(new Map())
  const reasonings: Ref<ReasoningEntry[]> = ref([])
  const isLoadingHistory: Ref<boolean> = ref(false)
  const agentPhase: Ref<AgentPhase> = ref('idle')
  const currentActivity: Ref<string> = ref('')

  const approvalPending: Ref<{
    request_id: string
    tool_name: string
    params: Record<string, unknown>
    reason: string
    chat_id: string
  } | null> = ref(null)

  function sendMessage(text: string, chatId: string | undefined, options?: { model?: string; maxTurns?: number }): void {
    approvalPending.value = null
    currentActivity.value = ''

    const isNewChat = !chatId

    const userMsg: Message = {
      message_id: crypto.randomUUID(),
      chat_id: chatId ?? '',
      timestamp: new Date().toISOString(),
      type: 'user',
      content: text,
    }
    messages.value = [...messages.value, userMsg]
    reasonings.value = []
    agentPhase.value = 'thinking'

    let currentAssistantMsgId = ''
    let assistantBuffer = ''
    let currentReasoningId = ''
    let reasoningBuffer = ''

    connect(
      { chatId, message: text, model: options?.model, maxTurns: options?.maxTurns },
      {
        onReasoning(data: ReasoningEvent) {
          // Show last 50 chars of reasoning as activity
          const text = data.delta
          if (text) {
            currentActivity.value = text.length > 50 ? '…' + text.slice(-50) : text
          }
          if (data.done) {
            // Batch reasoning from emit_events — dedup by message_id
            const msgId = data.message_id
            const existing = msgId ? reasonings.value.find(r => r.message_id === msgId) : undefined
            if (existing) {
              reasonings.value = reasonings.value.map(r =>
                r.message_id === msgId ? { ...r, done: true } : r
              )
            } else {
              reasonings.value = [...reasonings.value, {
                message_id: data.message_id ?? crypto.randomUUID(),
                chat_id: data.chat_id ?? chatId ?? '',
                content: data.delta,
                done: true,
                timestamp: new Date().toISOString(),
              }]
            }
            return
          }
          // Streaming reasoning delta
          if (!currentReasoningId) {
            currentReasoningId = crypto.randomUUID()
            reasoningBuffer = ''
          }
          reasoningBuffer += data.delta
          const existingIdx = reasonings.value.findIndex(r => r.message_id === currentReasoningId)
          if (existingIdx !== -1) {
            const updated = [...reasonings.value]
            updated[existingIdx] = { ...updated[existingIdx]!, content: reasoningBuffer }
            reasonings.value = updated
          } else {
            reasonings.value = [...reasonings.value, {
              message_id: currentReasoningId,
              chat_id: data.chat_id ?? chatId ?? '',
              content: reasoningBuffer,
              done: false,
              timestamp: new Date().toISOString(),
            }]
          }
        },

        onAssistant(data: AssistantEvent) {
          // Finalize any current reasoning entry
          if (currentReasoningId) {
            reasonings.value = reasonings.value.map(r =>
              r.message_id === currentReasoningId ? { ...r, done: true } : r
            )
            currentReasoningId = ''
            reasoningBuffer = ''
          }
          agentPhase.value = 'responding'
          currentActivity.value = ''

          if (data.message_id !== currentAssistantMsgId) {
            currentAssistantMsgId = data.message_id
            assistantBuffer = ''
            messages.value = [...messages.value, {
              message_id: data.message_id,
              chat_id: data.chat_id,
              timestamp: new Date().toISOString(),
              type: 'assistant',
              content: '',
            }]
          }
          assistantBuffer += data.delta
          const idx = messages.value.findIndex(m => m.message_id === currentAssistantMsgId)
          if (idx !== -1) {
            const updated = [...messages.value]
            updated[idx] = { ...updated[idx]!, content: assistantBuffer }
            messages.value = updated
          }
        },

        onToolCall(data: ToolCallEvent) {
          agentPhase.value = 'calling_tool'
          currentActivity.value = data.tool_name

          const tc: ToolCallInfo = {
            message_id: data.message_id,
            chat_id: data.chat_id,
            tool_name: data.tool_name,
            server: data.server,
            is_read_only: data.is_read_only,
            params: data.params,
            execution_status: 'RUNNING',
            timestamp: new Date().toISOString(),
          }
          const updated = new Map(toolCalls.value)
          updated.set(data.message_id, tc)
          toolCalls.value = updated
        },

        onToolResult(data: ToolResultEvent) {
          const existing = toolCalls.value.get(data.message_id)
          if (existing) {
            const updated = new Map(toolCalls.value)
            updated.set(data.message_id, {
              ...existing,
              execution_status: data.execution_status,
              output: data.output,
              execution_time_ms: data.execution_time_ms,
              ...(data.error ? { error: data.error } : {}),
            })
            toolCalls.value = updated
          }
        },

        onToolApprovalRequired(data: ToolApprovalRequiredEvent) {
          approvalPending.value = { ...data }
          agentPhase.value = 'awaiting_approval'
          currentActivity.value = data.tool_name
        },

        onError(data: ErrorEvent) {
          showToast('error', `${data.code}: ${data.message}`, data.code)
        },

        onDone(_data: DoneEvent) {
          agentPhase.value = 'done'
          currentActivity.value = ''
          if (isNewChat && _data.chat_id) {
            options?.onChatCreated?.(_data.chat_id)
          }
        },
      },
    )
  }

  async function submitApproval(
    requestId: string,
    status: 'APPROVED' | 'REJECTED',
    reason?: string
  ): Promise<void> {
    const pending = approvalPending.value

    try {
      const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approval_status: status, reason }),
      })
      if (res.ok) {
        approvalPending.value = null
        if (status === 'REJECTED') {
          messages.value = [...messages.value, {
            message_id: crypto.randomUUID(),
            chat_id: pending?.chat_id ?? '',
            timestamp: new Date().toISOString(),
            type: 'system',
            content: 'Tool execution rejected',
            is_meta: true,
          }]
        }
        agentPhase.value = 'thinking'
        currentActivity.value = 'Processing…'
      } else {
        showToast('error', `Approval failed: server returned ${res.status}`)
      }
    } catch (err: unknown) {
      showToast('error', `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    }
  }

  async function loadHistory(chatId: string): Promise<boolean> {
    isLoadingHistory.value = true
    approvalPending.value = null
    agentPhase.value = 'idle'
    currentActivity.value = ''
    try {
      const res = await fetch(`/api/sessions/${chatId}`)
      if (!res.ok) {
        showToast('error', `Failed to load history (${res.status})`)
        return false
      }
      const session: ChatSession = await res.json()
      messages.value = session.messages ?? []
      const map = new Map<string, ToolCallInfo>()
      if (session.executed_tool_list) {
        for (const tc of session.executed_tool_list) {
          map.set(tc.message_id, tc)
        }
      }
      toolCalls.value = map
      reasonings.value = []
      return true
    } catch {
      showToast('error', 'Failed to load conversation history')
      return false
    } finally {
      isLoadingHistory.value = false
    }
  }

  const phaseLabel = computed(() => {
    const detail = currentActivity.value
    switch (agentPhase.value) {
      case 'thinking':
        return detail ? `Thinking: ${detail}` : 'Thinking…'
      case 'calling_tool':
        return detail ? `Calling: ${detail}` : 'Calling tool…'
      case 'awaiting_approval':
        return detail ? `Approval needed: ${detail}` : 'Awaiting approval…'
      case 'responding':
        return 'Responding…'
      case 'done':
        return ''
      case 'idle':
      default:
        return ''
    }
  })

  function resetChat(): void {
    messages.value = []
    toolCalls.value = new Map()
    reasonings.value = []
    agentPhase.value = 'idle'
    currentActivity.value = ''
  }

  return {
    messages,
    toolCalls,
    reasonings,
    isStreaming,
    agentPhase,
    phaseLabel,
    currentActivity,
    approvalPending,
    isLoadingHistory,
    sendMessage,
    submitApproval,
    loadHistory,
    resetChat,
    abort,
  }
}
