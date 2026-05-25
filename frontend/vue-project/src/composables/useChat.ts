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

export function useChat(options?: { on_chat_created?: (chat_id: string) => void }) {
  const { connect, abort: sseAbort, isStreaming: is_streaming } = useSSE()
  const { showToast } = useToast()

  function abort(): void {
    sseAbort()
    approval_pending.value = null
    current_activity.value = ''
    agent_phase.value = 'idle'
    reasonings.value = reasonings.value.map(r => ({ ...r, done: true }))
  }

  const messages: Ref<Message[]> = ref([])
  const toolCalls: Ref<Map<string, ToolCallInfo>> = ref(new Map())
  const reasonings: Ref<ReasoningEntry[]> = ref([])
  const isLoadingHistory: Ref<boolean> = ref(false)
  const agent_phase: Ref<AgentPhase> = ref('idle')
  const current_activity: Ref<string> = ref('')

  const approval_pending: Ref<{
    request_id: string
    tool_name: string
    params: Record<string, unknown>
    reason: string
    chat_id: string
  } | null> = ref(null)

  function sendMessage(text: string, chat_id: string | undefined, options?: { model?: string; maxTurns?: number }): void {
    approval_pending.value = null
    current_activity.value = ''

    const isNewChat = !chat_id

    const userMsg: Message = {
      message_id: crypto.randomUUID(),
      chat_id: chat_id ?? '',
      timestamp: new Date().toISOString(),
      type: 'user',
      content: text,
    }
    messages.value = [...messages.value, userMsg]
    reasonings.value = []
    agent_phase.value = 'thinking'

    let current_assistant_msg_id = ''
    let assistant_buffer = ''
    let current_reasoning_id = ''
    let reasoning_buffer = ''

    connect(
      { chat_id, message: text, model: options?.model, max_turns: options?.maxTurns },
      {
        on_reasoning(data: ReasoningEvent) {
          const text = data.delta
          if (text) {
            current_activity.value = text.length > 50 ? '…' + text.slice(-50) : text
          }
          if (data.done) {
            const msgId = data.message_id ?? ''
            const existing = msgId ? reasonings.value.find(r => r.message_id === msgId) : undefined
            if (existing) {
              reasonings.value = reasonings.value.map(r =>
                r.message_id === msgId ? { ...r, done: true } : r
              )
            } else {
              reasonings.value = [...reasonings.value, {
                message_id: data.message_id ?? crypto.randomUUID(),
                chat_id: data.chat_id ?? chat_id ?? '',
                content: data.delta,
                done: true,
                timestamp: new Date().toISOString(),
              }]
            }
            return
          }
          if (!current_reasoning_id) {
            current_reasoning_id = crypto.randomUUID()
            reasoning_buffer = ''
          }
          reasoning_buffer += data.delta
          const existing_idx = reasonings.value.findIndex(r => r.message_id === current_reasoning_id)
          if (existing_idx !== -1) {
            const updated = [...reasonings.value]
            updated[existing_idx] = { ...updated[existing_idx]!, content: reasoning_buffer }
            reasonings.value = updated
          } else {
            reasonings.value = [...reasonings.value, {
              message_id: current_reasoning_id,
              chat_id: data.chat_id ?? chat_id ?? '',
              content: reasoning_buffer,
              done: false,
              timestamp: new Date().toISOString(),
            }]
          }
        },

        on_assistant(data: AssistantEvent) {
          if (current_reasoning_id) {
            reasonings.value = reasonings.value.map(r =>
              r.message_id === current_reasoning_id ? { ...r, done: true } : r
            )
            current_reasoning_id = ''
            reasoning_buffer = ''
          }
          agent_phase.value = 'responding'
          current_activity.value = ''

          if (data.message_id !== current_assistant_msg_id) {
            current_assistant_msg_id = data.message_id
            assistant_buffer = ''
            messages.value = [...messages.value, {
              message_id: data.message_id,
              chat_id: data.chat_id,
              timestamp: new Date().toISOString(),
              type: 'assistant',
              content: '',
            }]
          }
          assistant_buffer += data.delta
          const idx = messages.value.findIndex(m => m.message_id === current_assistant_msg_id)
          if (idx !== -1) {
            const updated = [...messages.value]
            updated[idx] = { ...updated[idx]!, content: assistant_buffer }
            messages.value = updated
          }
        },

        on_tool_call(data: ToolCallEvent) {
          agent_phase.value = 'calling_tool'
          current_activity.value = data.tool_name

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

        on_tool_result(data: ToolResultEvent) {
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

        on_tool_approval_required(data: ToolApprovalRequiredEvent) {
          approval_pending.value = { ...data }
          agent_phase.value = 'awaiting_approval'
          current_activity.value = data.tool_name
        },

        on_error(data: ErrorEvent) {
          showToast('error', `${data.code}: ${data.message}`, data.code)
        },

        on_done(_data: DoneEvent) {
          agent_phase.value = 'done'
          current_activity.value = ''
          if (isNewChat && _data.chat_id) {
            options?.on_chat_created?.(_data.chat_id)
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
    const pending = approval_pending.value

    try {
      const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approval_status: status, reason }),
      })
      if (res.ok) {
        approval_pending.value = null
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
        agent_phase.value = 'thinking'
        current_activity.value = 'Processing…'
      } else {
        showToast('error', `Approval failed: server returned ${res.status}`)
      }
    } catch (err: unknown) {
      showToast('error', `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    }
  }

  async function loadHistory(chatId: string): Promise<boolean> {
    isLoadingHistory.value = true
    approval_pending.value = null
    agent_phase.value = 'idle'
    current_activity.value = ''
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
    const detail = current_activity.value
    switch (agent_phase.value) {
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
    agent_phase.value = 'idle'
    current_activity.value = ''
  }

  return {
    messages,
    toolCalls,
    reasonings,
    is_streaming,
    agent_phase,
    phaseLabel,
    current_activity,
    approval_pending,
    isLoadingHistory,
    sendMessage,
    submitApproval,
    loadHistory,
    resetChat,
    abort,
  }
}
