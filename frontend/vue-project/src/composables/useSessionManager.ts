import { ref, computed, type Ref, type ComputedRef } from 'vue'
import type { Message, ToolCallInfo, ChatSession, SSECallbacks } from '@/types'
import { fetchEventSource } from '@microsoft/fetch-event-source'

export interface ApprovalEvent {
  request_id: string
  tool_name: string
  params: Record<string, unknown>
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  message: string
}

export interface ReasoningEntry {
  message_id: string
  chat_id: string
  content: string
  done: boolean
  timestamp: string
}

export type AgentPhase = 'idle' | 'thinking' | 'calling_tool' | 'awaiting_approval' | 'responding' | 'done'

export interface SessionState {
  chatId: string | null
  messages: Ref<Message[]>
  toolCalls: Ref<Map<string, ToolCallInfo>>
  reasonings: Ref<ReasoningEntry[]>
  isStreaming: Ref<boolean>
  draftInput: Ref<string>
  approvalEvent: Ref<ApprovalEvent | null>
  agentPhase: Ref<AgentPhase>
  currentActivity: Ref<string>
  phaseLabel: ComputedRef<string>
  isLoadingHistory: Ref<boolean>
  connectionError: Ref<string | null>
  sendMessage(text: string): void
  abort(): void
  approve(requestId: string, message?: string): Promise<void>
  reject(requestId: string, message?: string): Promise<void>
  loadHistory(): Promise<boolean>
}

type OnSessionIdCb = (chatId: string) => void

type SSEConnectFn = (body: Record<string, unknown>, callbacks: SSECallbacks, signal: AbortSignal, onSessionId?: OnSessionIdCb) => Promise<void>

function realSSEConnect(body: Record<string, unknown>, callbacks: SSECallbacks, signal: AbortSignal, onSessionId?: OnSessionIdCb): Promise<void> {
  return fetchEventSource('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
    openWhenHidden: true,
    onopen(response) {
      const sid = response.headers.get('X-Session-ID')
      if (sid) onSessionId?.(sid)
    },
    onmessage(msg) {
      if (!msg.event || !msg.data) return
      try {
        const parsed = JSON.parse(msg.data)
        const cb = callbacks[`on_${msg.event}` as keyof SSECallbacks]
        cb?.(parsed as never)
      } catch { /* malformed event, skip */ }
    },
    onerror(err) {
      callbacks.on_error?.({ code: 'NETWORK_ERROR', message: String(err) })
      throw err
    },
  })
}

interface ManagerDeps {
  _connect?: SSEConnectFn
}

export function useSessionManager(deps?: ManagerDeps) {
  const connectFn = deps?._connect ?? realSSEConnect

  const sessions: Ref<ChatSession[]> = ref([])
  const activeChatId: Ref<string | null> = ref(null)
  const instances = new Map<string | null, SessionState>()

  const isLoadingSessions: Ref<boolean> = ref(false)
  const loadError: Ref<string> = ref('')

  function createDraft(): void {
    activeChatId.value = null
  }

  function get(chatId: string | null): SessionState {
    let state = instances.get(chatId)
    if (!state) {
      state = _buildSessionState(chatId)
      instances.set(chatId, state)
    }
    return state
  }

  function deleteSession(chatId: string): void {
    const state = instances.get(chatId)
    if (state) {
      state.abort()
      instances.delete(chatId)
    }
    sessions.value = sessions.value.filter(s => s.chat_id !== chatId)
    if (activeChatId.value === chatId) {
      activeChatId.value = null
    }
  }

  async function loadSessions(): Promise<void> {
    isLoadingSessions.value = true
    loadError.value = ''
    try {
      const res = await fetch('/api/sessions')
      if (!res.ok) {
        loadError.value = `Server error (${res.status})`
        return
      }
      sessions.value = await res.json()
    } catch {
      loadError.value = 'Cannot connect to server'
    } finally {
      isLoadingSessions.value = false
    }
  }

  async function loadHistory(chatId: string): Promise<boolean> {
    activeChatId.value = chatId
    const state = get(chatId)
    return state.loadHistory()
  }

  function _transitionDraftToReal(realChatId: string, oldState: SessionState): void {
    instances.delete(null)
    oldState.chatId = realChatId
    instances.set(realChatId, oldState)
  }

  function _buildSessionState(initialChatId: string | null): SessionState {
    const chatId = ref<string | null>(initialChatId)
    const messages: Ref<Message[]> = ref([])
    const toolCalls: Ref<Map<string, ToolCallInfo>> = ref(new Map()) as Ref<Map<string, ToolCallInfo>>
    const reasonings: Ref<ReasoningEntry[]> = ref([])
    const isStreaming: Ref<boolean> = ref(false)
    const draftInput: Ref<string> = ref('')
    const approvalEvent: Ref<ApprovalEvent | null> = ref(null)
    const agentPhase: Ref<AgentPhase> = ref('idle')
    const currentActivity: Ref<string> = ref('')
    const isLoadingHistory: Ref<boolean> = ref(false)
    const connectionError: Ref<string | null> = ref(null)
    let abortController: AbortController | null = null

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
        case 'idle':
        default:
          return ''
      }
    })

    function sendMessage(text: string): void {
      const isNewChat = chatId.value === null
      approvalEvent.value = null
      currentActivity.value = ''
      connectionError.value = null

      const userMsg: Message = {
        message_id: crypto.randomUUID(),
        chat_id: chatId.value ?? '',
        timestamp: new Date().toISOString(),
        type: 'user',
        content: text,
      }
      messages.value = [...messages.value, userMsg]
      reasonings.value = []
      agentPhase.value = 'thinking'
      isStreaming.value = true

      abortController = new AbortController()

      let currentAssistantMsgId = ''
      let assistantBuffer = ''
      let currentReasoningId = ''
      let reasoningBuffer = ''
      let streamingBuffer = ''
      const STREAMING_PLACEHOLDER_ID = '__streaming__'

      let newChatId: string | null = null

      connectFn(
        { chat_id: chatId.value ?? undefined, message: text },
        {
          on_reasoning(data: any) {
            const delta = data.delta
            if (delta) {
              currentActivity.value = delta.length > 50 ? '…' + delta.slice(-50) : delta
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
                  chat_id: data.chat_id ?? chatId.value ?? '',
                  content: delta,
                  done: true,
                  timestamp: new Date().toISOString(),
                }]
              }
              return
            }
            if (!currentReasoningId) {
              currentReasoningId = crypto.randomUUID()
              reasoningBuffer = ''
            }
            reasoningBuffer += delta
            const idx = reasonings.value.findIndex(r => r.message_id === currentReasoningId)
            if (idx !== -1) {
              const updated = [...reasonings.value]
              updated[idx] = { ...updated[idx]!, content: reasoningBuffer }
              reasonings.value = updated
            } else {
              reasonings.value = [...reasonings.value, {
                message_id: currentReasoningId,
                chat_id: data.chat_id ?? chatId.value ?? '',
                content: reasoningBuffer,
                done: false,
                timestamp: new Date().toISOString(),
              }]
            }
          },

          on_assistant(data: any) {
            if (currentReasoningId) {
              reasonings.value = reasonings.value.map(r =>
                r.message_id === currentReasoningId ? { ...r, done: true } : r
              )
              currentReasoningId = ''
              reasoningBuffer = ''
            }
            agentPhase.value = 'responding'
            currentActivity.value = ''

            if (data.message_id) {
              // Formal message from emit_events (has message_id).
              // Replace streaming placeholder if it exists.
              streamingBuffer = ''
              const placeholderIdx = messages.value.findIndex(m => m.message_id === STREAMING_PLACEHOLDER_ID)
              if (data.message_id !== currentAssistantMsgId) {
                currentAssistantMsgId = data.message_id
                assistantBuffer = ''
                const newMsg: Message = {
                  message_id: data.message_id,
                  chat_id: data.chat_id ?? chatId.value ?? '',
                  timestamp: new Date().toISOString(),
                  type: 'assistant',
                  content: '',
                }
                if (placeholderIdx !== -1) {
                  // Replace placeholder with formal message.
                  const updated = [...messages.value]
                  updated[placeholderIdx] = newMsg
                  messages.value = updated
                } else {
                  messages.value = [...messages.value, newMsg]
                }
              }
              assistantBuffer += data.delta
              const idx = messages.value.findIndex(m => m.message_id === currentAssistantMsgId)
              if (idx !== -1) {
                const updated = [...messages.value]
                updated[idx] = { ...updated[idx]!, content: assistantBuffer }
                messages.value = updated
              }
            } else {
              // Streaming delta (no message_id) — show as temporary placeholder.
              streamingBuffer += data.delta
              const placeholderIdx = messages.value.findIndex(m => m.message_id === STREAMING_PLACEHOLDER_ID)
              if (placeholderIdx === -1) {
                messages.value = [...messages.value, {
                  message_id: STREAMING_PLACEHOLDER_ID,
                  chat_id: chatId.value ?? '',
                  timestamp: new Date().toISOString(),
                  type: 'assistant',
                  content: streamingBuffer,
                }]
              } else {
                const updated = [...messages.value]
                updated[placeholderIdx] = { ...updated[placeholderIdx]!, content: streamingBuffer }
                messages.value = updated
              }
            }
          },

          on_tool_call(data: any) {
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

          on_tool_result(data: any) {
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

          on_tool_approval_required(data: any) {
            approvalEvent.value = {
              request_id: data.request_id,
              tool_name: data.tool_name,
              params: data.params,
              reason: data.reason,
              status: 'pending',
              message: '',
            }
            agentPhase.value = 'awaiting_approval'
            currentActivity.value = data.tool_name
          },

          on_done(_data: any) {
            agentPhase.value = 'done'
            currentActivity.value = ''
            isStreaming.value = false
            if (isNewChat && newChatId) {
              _transitionDraftToReal(newChatId, _self)
              sessions.value = [{
                chat_id: newChatId,
                messages: messages.value,
                executed_tool_list: [],
                timestamp: new Date().toISOString(),
              }, ...sessions.value]
              activeChatId.value = newChatId
            }
          },

          on_error(data: any) {
            isStreaming.value = false
            connectionError.value = data.code ? `${data.code}: ${data.message}` : String(data)
          },
        },
        abortController.signal,
        (sid: string) => { if (isNewChat) newChatId = sid },
      ).catch((err: unknown) => {
        isStreaming.value = false
        if (err instanceof DOMException && err.name === 'AbortError') return
        connectionError.value = err instanceof Error ? `NETWORK_ERROR: ${err.message}` : 'NETWORK_ERROR: Connection lost'
      })
    }

    function abort(): void {
      abortController?.abort()
      isStreaming.value = false
      agentPhase.value = 'idle'
      currentActivity.value = ''
      reasonings.value = reasonings.value.map(r => ({ ...r, done: true }))
    }

    async function approve(requestId: string, message?: string): Promise<void> {
      try {
        const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_status: 'APPROVED', reason: message || '' }),
        })
        if (res.ok && approvalEvent.value) {
          approvalEvent.value = { ...approvalEvent.value, status: 'approved', message: message || '' }
          agentPhase.value = 'thinking'
          currentActivity.value = 'Processing…'
        } else if (!res.ok) {
          connectionError.value = `Approval failed: server returned ${res.status}`
        }
      } catch (err: unknown) {
        connectionError.value = `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`
      }
    }

    async function reject(requestId: string, message?: string): Promise<void> {
      try {
        const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_status: 'REJECTED', reason: message || '' }),
        })
        if (res.ok && approvalEvent.value) {
          approvalEvent.value = { ...approvalEvent.value, status: 'rejected', message: message || '' }
          agentPhase.value = 'thinking'
          currentActivity.value = 'Processing…'
        } else if (!res.ok) {
          connectionError.value = `Approval failed: server returned ${res.status}`
        }
      } catch (err: unknown) {
        connectionError.value = `Approval failed: ${err instanceof Error ? err.message : 'Unknown error'}`
      }
    }

    async function loadHistory(): Promise<boolean> {
      isLoadingHistory.value = true
      approvalEvent.value = null
      agentPhase.value = 'idle'
      currentActivity.value = ''
      connectionError.value = null

      if (chatId.value === null) {
        messages.value = []
        toolCalls.value = new Map()
        reasonings.value = []
        isLoadingHistory.value = false
        return true
      }

      try {
        const res = await fetch(`/api/sessions/${chatId.value}`)
        if (!res.ok) {
          isLoadingHistory.value = false
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
        isLoadingHistory.value = false
        return true
      } catch {
        isLoadingHistory.value = false
        return false
      }
    }

    const _self: SessionState = {
      get chatId() { return chatId.value },
      set chatId(v: string | null) { chatId.value = v },
      messages,
      toolCalls,
      reasonings,
      isStreaming,
      draftInput,
      approvalEvent,
      agentPhase,
      currentActivity,
      phaseLabel,
      isLoadingHistory,
      connectionError,
      sendMessage,
      abort,
      approve,
      reject,
      loadHistory,
    }

    return _self
  }

  return {
    sessions,
    activeChatId,
    isLoadingSessions,
    loadError,
    createDraft,
    get,
    loadSessions,
    loadHistory,
    deleteSession,
  }
}
