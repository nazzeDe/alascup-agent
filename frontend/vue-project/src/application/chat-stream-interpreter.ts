import { ChatStore } from '@/application/chat-store'
import type { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import type { ToastStore } from '@/application/toast-store'
import type { ChatStreamEvent } from '@/domain/sse-events'
import type { Message, ToolCallInfo } from '@/domain/models'

export interface ChatStreamInterpreterOptions {
  chatStore: ChatStore
  activeSessionWorkspace: Pick<ActiveSessionWorkspace, 'adoptServerSession'>
  toastStore: ToastStore
  isNewChat: boolean
  firstMessageTitle: string
  idFactory?: () => string
  now?: () => string
  toolWaitMs?: number
}

export class ChatStreamInterpreter {
  private sessionChatId: string
  private currentReasoningId = ''
  private reasoningBuffer = ''
  private currentAssistantId = ''
  private newChatId: string | null = null
  private toolTimeout: ReturnType<typeof setTimeout> | null = null
  private readonly idFactory: () => string
  private readonly now: () => string
  private readonly toolWaitMs: number

  constructor(private readonly options: ChatStreamInterpreterOptions) {
    this.sessionChatId = options.chatStore.chatId.value ?? ''
    this.idFactory = options.idFactory ?? (() => crypto.randomUUID())
    this.now = options.now ?? (() => new Date().toISOString())
    this.toolWaitMs = options.toolWaitMs ?? 800
  }

  apply(event: ChatStreamEvent): void {
    switch (event.type) {
      case 'session_init':
        this.applySessionInit(event.data.chat_id)
        break
      case 'reasoning':
        this.applyReasoning(event.data.delta)
        break
      case 'thinking_done':
        this.finishReasoning()
        this.options.chatStore.setPhase('responding')
        break
      case 'assistant':
        this.finishReasoning()
        this.options.chatStore.setPhase('responding')
        this.appendAssistantDelta(event.data.delta)
        break
      case 'assistant_done':
        this.finishAssistant()
        break
      case 'tool_call':
        this.applyToolCall(event.data)
        break
      case 'tool_result':
        this.applyToolResult(event.data)
        break
      case 'tool_approval_required':
        this.applyApprovalRequired(event.data)
        break
      case 'done':
        this.done()
        break
      case 'error':
        this.error(event.data.code, event.data.message)
        break
    }
  }

  abort(): void {
    this.clearToolTimeout()
    const store = this.options.chatStore
    store.setStreaming(false)
    store.setPhase('idle')
    store.reasonings.value = store.reasonings.value.map(r => ({ ...r, done: true }))
  }

  completeConnection(): void {
    const store = this.options.chatStore
    if (store.isStreaming.value) {
      store.setStreaming(false)
      store.setPhase('idle')
    }
  }

  private applySessionInit(chatId: string): void {
    this.sessionChatId = chatId
    if (!this.options.isNewChat) return
    this.newChatId = chatId
    this.options.activeSessionWorkspace.adoptServerSession(chatId, this.options.firstMessageTitle)
  }

  private applyReasoning(delta: string): void {
    if (!this.currentReasoningId) {
      this.currentReasoningId = this.idFactory()
      this.reasoningBuffer = ''
    }
    this.reasoningBuffer += delta
    this.updateReasoningContent(this.currentReasoningId, this.reasoningBuffer)
  }

  private finishReasoning(): void {
    if (!this.currentReasoningId) return
    this.options.chatStore.markReasoningDone(this.currentReasoningId)
    this.currentReasoningId = ''
    this.reasoningBuffer = ''
  }

  private appendAssistantDelta(delta: string): void {
    if (!this.currentAssistantId) {
      this.currentAssistantId = this.idFactory()
      const msg: Message = {
        message_id: this.currentAssistantId,
        chat_id: this.sessionChatId,
        timestamp: this.now(),
        type: 'assistant',
        content: '',
      }
      this.options.chatStore.addMessage(msg)
    }

    const store = this.options.chatStore
    const existing = store.messages.value.find(m => m.message_id === this.currentAssistantId)
    store.updateMessage(this.currentAssistantId, {
      content: (existing?.content ?? '') + delta,
    })
  }

  private finishAssistant(): void {
    if (!this.currentAssistantId) return
    this.currentAssistantId = ''
  }

  private applyToolCall(data: Extract<ChatStreamEvent, { type: 'tool_call' }>['data']): void {
    const store = this.options.chatStore
    store.setPhase('calling_tool')

    const tc: ToolCallInfo = {
      call_id: data.call_id,
      chat_id: this.sessionChatId,
      tool_name: data.tool_name,
      server: data.server,
      is_read_only: data.is_read_only,
      params: data.params,
      execution_status: 'RUNNING',
      timestamp: this.now(),
    }
    store.setToolCall(data.call_id, tc)

    this.clearToolTimeout()
    this.toolTimeout = setTimeout(() => {
      if (store.agentPhase.value === 'calling_tool') {
        store.setPhase('waiting_for_tool')
      }
    }, this.toolWaitMs)
  }

  private applyToolResult(data: Extract<ChatStreamEvent, { type: 'tool_result' }>['data']): void {
    const store = this.options.chatStore
    store.setPhase('thinking')
    this.clearToolTimeout()
    const existing = store.toolCalls.value.get(data.call_id)
    if (!existing) {
      console.warn('[SSE] tool_result: unknown call_id', data.call_id)
      return
    }
    store.updateToolCall(data.call_id, {
      execution_status: data.execution_status,
      output: data.output,
      execution_time_ms: data.execution_time_ms,
      ...(data.error ? { error: data.error } : {}),
    })
  }

  private applyApprovalRequired(data: Extract<ChatStreamEvent, { type: 'tool_approval_required' }>['data']): void {
    const store = this.options.chatStore
    store.setApprovalEvent({
      request_id: data.request_id,
      tool_name: data.tool_name,
      params: data.params,
      reason: data.reason,
      status: 'pending',
      message: '',
      timestamp: this.now(),
    })
    store.setPhase('awaiting_approval')
    const existing = store.toolCalls.value.get(data.call_id)
    if (existing && existing.execution_status === 'RUNNING') {
      store.updateToolCall(data.call_id, { execution_status: 'PENDING_APPROVAL' })
    }
  }

  private done(): void {
    const store = this.options.chatStore
    store.setPhase('done')
    store.setStreaming(false)
    if (!store.connectionError.value) {
      store.setConnectionError(null)
    }
    if (this.options.isNewChat && !this.newChatId) {
      this.options.toastStore.show('error', '发送失败，请刷新页面重试')
    }
  }

  private error(code: string, message: string): void {
    const store = this.options.chatStore
    store.setStreaming(false)
    store.setPhase('idle')
    store.setConnectionError({ code: code || 'UNKNOWN', message })
    if (this.options.isNewChat) {
      this.options.toastStore.show('error', '发送失败，请刷新页面重试')
    }
  }

  private updateReasoningContent(messageId: string, fullContent: string): void {
    const store = this.options.chatStore
    const idx = store.reasonings.value.findIndex(r => r.message_id === messageId)
    if (idx !== -1) {
      const updated = [...store.reasonings.value]
      updated[idx] = { ...updated[idx]!, content: fullContent }
      store.reasonings.value = updated
      return
    }
    store.reasonings.value = [...store.reasonings.value, {
      message_id: messageId,
      content: fullContent,
      done: false,
      timestamp: this.now(),
    }]
  }

  private clearToolTimeout(): void {
    if (!this.toolTimeout) return
    clearTimeout(this.toolTimeout)
    this.toolTimeout = null
  }
}
