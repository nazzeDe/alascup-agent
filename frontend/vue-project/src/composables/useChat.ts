import { ref, computed, type Ref, type ComputedRef } from 'vue'
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
import { useSessions } from './useSessions'
import { useToast } from './useToast'

export type AgentPhase = 'idle' | 'thinking' | 'calling_tool' | 'responding' | 'done'

interface QueuedEvent {
  type: 'assistant' | 'reasoning' | 'tool_call' | 'tool_result' | 'error' | 'done'
  data: unknown
}

export interface ReasoningEntry {
  message_id: string
  chat_id: string
  content: string
  done: boolean
  timestamp: string
}

export function useChat() {
  const { connect, abort: sseAbort, isStreaming } = useSSE()
  const { activeChatId, loadSessions } = useSessions()
  const { showToast } = useToast()

  const messages: Ref<Message[]> = ref([])
  const toolCalls: Ref<Map<string, ToolCallInfo>> = ref(new Map())
  const reasonings: Ref<ReasoningEntry[]> = ref([])
  const isLoadingHistory: Ref<boolean> = ref(false)
  const agentPhase: Ref<AgentPhase> = ref('idle')

  const approvalPending: Ref<{
    request_id: string
    tool_name: string
    params: Record<string, unknown>
    reason: string
    chat_id: string
  } | null> = ref(null)

  let isApprovalPaused = false
  let queuedEvents: QueuedEvent[] = []

  function sendMessage(text: string, options?: { model?: string; maxTurns?: number }): void {
    const chatId = activeChatId.value

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
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'reasoning', data })
            return
          }
          agentPhase.value = 'thinking'
          if (data.done) {
            // Batch reasoning from emit_events (non-streaming path)
            const e: ReasoningEntry = {
              message_id: data.message_id ?? crypto.randomUUID(),
              chat_id: data.chat_id ?? chatId ?? '',
              content: data.delta,
              done: true,
              timestamp: new Date().toISOString(),
            }
            reasonings.value = [...reasonings.value, e]
            return
          }
          // Streaming reasoning
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
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'assistant', data })
            return
          }
          // Finalize current reasoning entry when assistant text starts
          if (currentReasoningId) {
            reasonings.value = reasonings.value.map(r =>
              r.message_id === currentReasoningId ? { ...r, done: true } : r
            )
            currentReasoningId = ''
            reasoningBuffer = ''
          }
          agentPhase.value = 'responding'
          // New message_id → start a new assistant message (e.g. after tool results)
          if (data.message_id !== currentAssistantMsgId) {
            currentAssistantMsgId = data.message_id
            assistantBuffer = ''
            const msg: Message = {
              message_id: data.message_id,
              chat_id: data.chat_id,
              timestamp: new Date().toISOString(),
              type: 'assistant',
              content: '',
            }
            messages.value = [...messages.value, msg]
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
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'tool_call', data })
            return
          }
          agentPhase.value = 'calling_tool'
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
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'tool_result', data })
            return
          }
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
          isApprovalPaused = true
          agentPhase.value = 'idle'

          const tc: ToolCallInfo = {
            message_id: crypto.randomUUID(),
            chat_id: data.chat_id,
            tool_name: data.tool_name,
            is_read_only: false,
            params: data.params,
            request_id: data.request_id,
            approval_status: 'PENDING',
            execution_status: 'PENDING_APPROVAL',
            timestamp: new Date().toISOString(),
          }
          const updated = new Map(toolCalls.value)
          updated.set(tc.message_id, tc)
          toolCalls.value = updated
        },

        onError(data: ErrorEvent) {
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'error', data })
            return
          }
          // FE-014: transient errors go to toast
          showToast('error', `${data.code}: ${data.message}`, data.code)
        },

        onDone(_data: DoneEvent) {
          if (isApprovalPaused) {
            queuedEvents.push({ type: 'done', data: _data })
            return
          }
          agentPhase.value = 'done'
          if (!activeChatId.value && _data.chat_id) {
            activeChatId.value = _data.chat_id
            loadSessions()
          }
        },
      },
    )
  }

  function drainQueue(): void {
    // Process queued events in order; re-dispatched through the same callbacks would re-queue, so process directly
    const events = queuedEvents
    queuedEvents = []
    isApprovalPaused = false

    for (const evt of events) {
      // Re-inject through the public callbacks by simulating inline dispatch
      switch (evt.type) {
        case 'reasoning': {
          const d = evt.data as ReasoningEvent
          reasonings.value = reasonings.value.map(r =>
            r.message_id === d.message_id ? { ...r, done: true } : r
          )
          break
        }
        case 'assistant': {
          const d = evt.data as AssistantEvent
          // Find or create assistant message
          const idx = messages.value.findIndex(m => m.message_id === d.message_id)
          if (idx !== -1) {
            const updated = [...messages.value]
            updated[idx] = { ...updated[idx]!, content: updated[idx]!.content + d.delta }
            messages.value = updated
          } else {
            messages.value = [...messages.value, {
              message_id: d.message_id,
              chat_id: d.chat_id,
              timestamp: new Date().toISOString(),
              type: 'assistant',
              content: d.delta,
            }]
          }
          break
        }
        case 'done': {
          const d = evt.data as DoneEvent
          if (!activeChatId.value && d.chat_id) {
            activeChatId.value = d.chat_id
            loadSessions()
          }
          break
        }
      }
    }
  }

  async function submitApproval(requestId: string, status: 'APPROVED' | 'REJECTED', reason?: string): Promise<void> {
    const pending = approvalPending.value
    approvalPending.value = null

    if (status === 'APPROVED') {
      // Show processing placeholder while approval finishes and agent responds
      const processingId = crypto.randomUUID()
      messages.value = [...messages.value, {
        message_id: processingId,
        chat_id: pending?.chat_id ?? activeChatId.value ?? '',
        timestamp: new Date().toISOString(),
        type: 'system',
        content: 'Processing…',
        is_meta: true,
      }]
      try {
        const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_status: status, reason }),
        })
        if (res.ok) {
          // drain queued events after approval
          if (pending) drainQueue()

          // Reload session history — replaces processing placeholder
          const chatId = pending?.chat_id ?? activeChatId.value
          if (chatId) {
            await loadHistory(chatId)
          }
        } else {
          // Remove processing placeholder on error
          messages.value = messages.value.filter(m => m.message_id !== processingId)
          showToast('error', `Approval failed: server returned ${res.status}`)
        }
      } catch (err: unknown) {
        messages.value = messages.value.filter(m => m.message_id !== processingId)
        showToast('error', `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
      }
    } else {
      // Rejection: just send, drain queue, and show meta message
      try {
        const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_status: status, reason }),
        })
        if (res.ok) {
          if (pending) drainQueue()
          messages.value = [...messages.value, {
            message_id: crypto.randomUUID(),
            chat_id: pending?.chat_id ?? activeChatId.value ?? '',
            timestamp: new Date().toISOString(),
            type: 'system',
            content: 'Tool execution rejected',
            is_meta: true,
          }]
        } else {
          showToast('error', `Approval failed: server returned ${res.status}`)
        }
      } catch (err: unknown) {
        showToast('error', `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
      }
    }
  }

  async function loadHistory(chatId: string): Promise<void> {
    isLoadingHistory.value = true
    try {
      const res = await fetch(`/api/sessions/${chatId}`)
      if (!res.ok) return
      const session: ChatSession = await res.json()
      messages.value = session.messages ?? []
      const map = new Map<string, ToolCallInfo>()
      if (session.executed_tool_list) {
        for (const tc of session.executed_tool_list) {
          map.set(tc.message_id, tc)
        }
      }
      toolCalls.value = map
    } catch {
      // silently fail
    } finally {
      isLoadingHistory.value = false
    }
  }

  const phaseLabel = computed(() => {
    switch (agentPhase.value) {
      case 'thinking': return 'Thinking…'
      case 'calling_tool': return 'Calling tool…'
      case 'responding': return 'Responding…'
      case 'done': return ''
      case 'idle': return ''
      default: return ''
    }
  })

  return {
    messages,
    toolCalls,
    reasonings,
    isStreaming,
    agentPhase,
    phaseLabel,
    approvalPending,
    isLoadingHistory,
    sendMessage,
    submitApproval,
    loadHistory,
    abort: sseAbort,
  }
}
