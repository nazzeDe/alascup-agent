import { ref, computed, provide, inject, type Ref, type ComputedRef, type InjectionKey } from 'vue'
import type { Message, ToolCallInfo, ChatSession, SSECallbacks } from '@/types'
import { fetchEventSource } from '@microsoft/fetch-event-source'

export interface SessionManager {
  sessions: Ref<ChatSession[]>
  activeChatId: Ref<string | null>
  isLoadingSessions: Ref<boolean>
  loadError: Ref<string>
  createDraft(): void
  get(chatId: string | null): SessionState
  loadSessions(): Promise<void>
  loadHistory(chatId: string): Promise<boolean>
  deleteSession(chatId: string): Promise<void>
}

const MANAGER_KEY: InjectionKey<SessionManager> = Symbol('sessionManager')

export interface ErrorInfo {
  code: string
  message: string
}

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
  content: string
  done: boolean
  timestamp: string
}

export type AgentPhase = 'idle' | 'thinking' | 'calling_tool' | 'waiting_for_tool' | 'awaiting_approval' | 'responding' | 'done'

export interface SessionState {
  chatId: string | null
  messages: Ref<Message[]>
  toolCalls: Ref<Map<string, ToolCallInfo>>
  reasonings: Ref<ReasoningEntry[]>
  isStreaming: Ref<boolean>
  draftInput: Ref<string>
  approvalEvent: Ref<ApprovalEvent | null>
  agentPhase: Ref<AgentPhase>
  phaseLabel: ComputedRef<string>
  isLoadingHistory: Ref<boolean>
  connectionError: Ref<ErrorInfo | null>
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
        // Debug: trace received SSE events
        if (msg.event === 'tool_call' || msg.event === 'tool_result') {
          console.log('[SSE] recv', msg.event, (parsed as any).call_id, (parsed as any).execution_status || (parsed as any).tool_name)
        } else if (msg.event === 'done') {
          console.log('[SSE] recv', msg.event)
        } else if (msg.event === 'error') {
          console.log('[SSE] recv', msg.event, (parsed as any).code)
        }
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
  // Singleton: inject parent-provided instance, avoiding dual-instance bugs.
  const existing = inject(MANAGER_KEY, null)
  if (existing) return existing

  const connectFn = deps?._connect ?? realSSEConnect

  const sessions: Ref<ChatSession[]> = ref([])
  const activeChatId: Ref<string | null> = ref(null)
  const instances = new Map<string | null, SessionState>()

  const isLoadingSessions: Ref<boolean> = ref(false)
  const loadError: Ref<string> = ref('')

  function createDraft(): void {
    activeChatId.value = null
    // Pre-create session on backend so it appears in sidebar immediately
    fetch('/api/sessions', { method: 'POST' })
      .then(resp => resp.ok ? resp.json() : null)
      .then(data => {
        if (data?.chat_id) {
          activeChatId.value = data.chat_id
          sessions.value = [{
            chat_id: data.chat_id,
            messages: [],
            executed_tool_list: [],
            timestamp: new Date().toISOString(),
          }, ...sessions.value]
        }
      })
      .catch(() => { /* fallback: session created on first sendMessage */ })
  }

  function get(chatId: string | null): SessionState {
    let state = instances.get(chatId)
    if (!state) {
      state = _buildSessionState(chatId)
      instances.set(chatId, state)
    }
    return state
  }

  async function deleteSession(chatId: string): Promise<void> {
    try {
      await fetch(`/api/sessions/${chatId}`, { method: 'DELETE' })
    } catch {
      // Best-effort: clean up locally even if backend unreachable
    }
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
    const isLoadingHistory: Ref<boolean> = ref(false)
    const connectionError: Ref<ErrorInfo | null> = ref(null)
    let abortController: AbortController | null = null
    let _toolTimeout: ReturnType<typeof setTimeout> | null = null

    const phaseLabel = computed(() => {
      switch (agentPhase.value) {
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

    function sendMessage(text: string): void {
      const isNewChat = chatId.value === null
      approvalEvent.value = null
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

      let sessionChatId = chatId.value ?? ''
      let currentReasoningId = ''
      let reasoningBuffer = ''
      let assistantBuffer = ''

      let newChatId: string | null = null

      connectFn(
        { chat_id: chatId.value ?? undefined, message: text },
        {
          on_session_init(data: any) {
            sessionChatId = data.chat_id
            if (isNewChat) newChatId = data.chat_id
          },

          on_reasoning(data: any) {
            const delta = data.delta
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
                content: reasoningBuffer,
                done: false,
                timestamp: new Date().toISOString(),
              }]
            }
          },

          on_thinking_done(_data: any) {
            if (currentReasoningId) {
              reasonings.value = reasonings.value.map(r =>
                r.message_id === currentReasoningId ? { ...r, done: true } : r
              )
              currentReasoningId = ''
              reasoningBuffer = ''
            }
            agentPhase.value = 'responding'
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
            assistantBuffer += data.delta
          },

          on_assistant_done(_data: any) {
            if (assistantBuffer) {
              const msg: Message = {
                message_id: crypto.randomUUID(),
                chat_id: sessionChatId,
                timestamp: new Date().toISOString(),
                type: 'assistant',
                content: assistantBuffer,
              }
              messages.value = [...messages.value, msg]
              assistantBuffer = ''
            }
          },

          on_tool_call(data: any) {
            console.log('[SSE] on_tool_call', data.call_id, data.tool_name, 'is_read_only:', data.is_read_only)
            agentPhase.value = 'calling_tool'

            const tc: ToolCallInfo = {
              call_id: data.call_id,
              chat_id: sessionChatId,
              tool_name: data.tool_name,
              server: data.server,
              is_read_only: data.is_read_only,
              params: data.params,
              execution_status: 'RUNNING',
              timestamp: new Date().toISOString(),
            }
            const updated = new Map(toolCalls.value)
            updated.set(data.call_id, tc)
            toolCalls.value = updated

            if (_toolTimeout) clearTimeout(_toolTimeout)
            _toolTimeout = setTimeout(() => {
              if (agentPhase.value === 'calling_tool') {
                agentPhase.value = 'waiting_for_tool'
              }
            }, 800)
          },

          on_tool_result(data: any) {
            console.log('[SSE] on_tool_result', data.call_id, data.execution_status)
            agentPhase.value = 'thinking'
            if (_toolTimeout) { clearTimeout(_toolTimeout); _toolTimeout = null }
            const existing = toolCalls.value.get(data.call_id)
            if (!existing) {
              console.warn('[SSE] on_tool_result: unknown call_id', data.call_id)
            }
            if (existing) {
              const updated = new Map(toolCalls.value)
              updated.set(data.call_id, {
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
            // Transition the specific tool to PENDING_APPROVAL by call_id
            // so on_done won't force-fail it while waiting for user decision.
            if (data.call_id) {
              const existing = toolCalls.value.get(data.call_id)
              if (existing && existing.execution_status === 'RUNNING') {
                const updated = new Map(toolCalls.value)
                updated.set(data.call_id, { ...existing, execution_status: 'PENDING_APPROVAL' })
                toolCalls.value = updated
              }
            }
          },

          on_done(_data: any) {
            // Debug: identify tools that never received a tool_result
            const runningTools = [...toolCalls.value.values()].filter(tc => tc.execution_status === 'RUNNING')
            if (runningTools.length > 0) {
              console.warn('[SSE] on_done: force-failing RUNNING tools:', runningTools.map(tc => `${tc.call_id}(${tc.tool_name})`))
            } else {
              console.log('[SSE] on_done: no RUNNING tools, clean exit')
            }
            agentPhase.value = 'done'
            isStreaming.value = false
            // Keep connectionError if set by preceding on_error (Bug 2: done after error)
            if (!connectionError.value) {
              connectionError.value = null
            }
            // Clean up any RUNNING tools that never finished (guard
            // against backend bugs that leave tool calls in RUNNING state).
            // Skip PENDING_APPROVAL tools — they are waiting for user decision,
            // not actually executing.
            const updated = new Map(toolCalls.value)
            for (const [id, tc] of updated) {
              if (tc.execution_status === 'RUNNING') {
                console.warn('[SSE] on_done: force-failing', id, tc.tool_name)
                updated.set(id, {
                  ...tc,
                  execution_status: 'FAILED',
                  error: { message: 'Connection closed before tool completed' },
                })
              }
            }
            toolCalls.value = updated
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
            agentPhase.value = 'idle'
            connectionError.value = {
              code: data.code || 'UNKNOWN',
              message: data.message || String(data),
            }
          },
        },
        abortController.signal,
        (sid: string) => { if (isNewChat) newChatId = sid },
      ).then(() => {
        if (isStreaming.value) {
          isStreaming.value = false
          agentPhase.value = 'idle'
        }
      }).catch((err: unknown) => {
        isStreaming.value = false
        if (err instanceof DOMException && err.name === 'AbortError') return
        connectionError.value = {
          code: 'NETWORK_ERROR',
          message: err instanceof Error ? err.message : 'Connection lost',
        }
      })
    }

    function abort(): void {
      abortController?.abort()
      isStreaming.value = false
      agentPhase.value = 'idle'
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
        } else if (!res.ok) {
          connectionError.value = { code: 'APPROVAL_FAILED', message: `Server returned ${res.status}` }
        }
      } catch (err: unknown) {
        connectionError.value = { code: 'APPROVAL_FAILED', message: err instanceof Error ? err.message : 'Unknown error' }
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
        } else if (!res.ok) {
          connectionError.value = { code: 'APPROVAL_FAILED', message: `Server returned ${res.status}` }
        }
      } catch (err: unknown) {
        connectionError.value = { code: 'APPROVAL_FAILED', message: err instanceof Error ? err.message : 'Unknown error' }
      }
    }

    async function loadHistory(): Promise<boolean> {
      isLoadingHistory.value = true
      approvalEvent.value = null
      agentPhase.value = 'idle'
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
            map.set(tc.call_id, tc)
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

  const manager = {
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
  provide(MANAGER_KEY, manager)
  return manager
}
