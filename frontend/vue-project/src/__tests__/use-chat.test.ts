import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, provide, shallowRef } from 'vue'

/** Flush the microtask queue so .catch() handlers run. */
function flushMicrotasks(): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, 0))
}
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { ToastStore } from '@/application/toast-store'
import type { SseClient } from '@/application/ports'
import type { SSECallbacks } from '@/domain/sse-events'
import { useChat } from '@/presentation/composables/use-chat'
import type {
  ToolCallEvent, ToolResultEvent, ToolApprovalRequiredEvent,
  SessionInitEvent, ErrorEvent,
} from '@/domain/sse-events'

// FakeSseClient captures callbacks so tests can drive SSE events manually.
class FakeSseClient implements SseClient {
  private _callbacks: SSECallbacks | null = null
  private _signal: AbortSignal | null = null
  private _onSessionId: ((chatId: string) => void) | null = null
  private _connectPromise: Promise<void> | null = null
  private _resolveConnect: (() => void) | null = null

  async connect(
    _body: Record<string, unknown>,
    callbacks: SSECallbacks,
    signal: AbortSignal,
    onSessionId?: (chatId: string) => void
  ): Promise<void> {
    this._callbacks = callbacks
    this._signal = signal
    this._onSessionId = onSessionId ?? null
    this._connectPromise = new Promise<void>((resolve) => {
      this._resolveConnect = resolve
    })
    return this._connectPromise
  }

  resolveConnect(): void {
    this._resolveConnect?.()
  }

  emit<T extends keyof SSECallbacks>(
    handlerName: T,
    data: Parameters<NonNullable<SSECallbacks[T]>>[0]
  ): void {
    const cb = this._callbacks?.[handlerName]
    if (cb) (cb as (data: unknown) => void)(data)
  }

  fireOnSessionId(chatId: string): void {
    this._onSessionId?.(chatId)
  }

  get signal(): AbortSignal | null {
    return this._signal
  }

  isAborted(): boolean {
    return this._signal?.aborted ?? false
  }
}

// Parent provides, Child injects — Vue's provide/inject only works ancestor→descendant.
function mountUseChat(
  fakeSse: FakeSseClient,
  store: ChatStore,
  sessionListStore: SessionListStore,
) {
  let chat: ReturnType<typeof useChat> | undefined
  const toastStore = new ToastStore()

  const Child = defineComponent({
    setup() {
      chat = useChat()
      return {}
    },
    template: '<div/>',
  })

  const Parent = defineComponent({
    setup() {
      const storeRef = shallowRef(store)
      provide('sseClient', fakeSse)
      provide('chatStore', storeRef)
      provide('sessionListStore', sessionListStore)
      provide('toastStore', toastStore)
      return {}
    },
    template: '<Child />',
    components: { Child },
  })

  const wrapper = mount(Parent)
  return { wrapper, getChat: () => chat! }
}

describe('useChat', () => {
  let fakeSse: FakeSseClient
  let chatStore: ChatStore
  let sessionListStore: SessionListStore

  beforeEach(() => {
    fakeSse = new FakeSseClient()
    chatStore = new ChatStore()
    sessionListStore = new SessionListStore()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // --- 1. sendMessage adds user message, sets isStreaming, phase='thinking' ---
  it('sendMessage adds user message, sets isStreaming, phase=thinking', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('chat-1')
    getChat().send('hello')

    expect(chatStore.messages.value).toHaveLength(1)
    expect(chatStore.messages.value[0]!.type).toBe('user')
    expect(chatStore.messages.value[0]!.content).toBe('hello')
    expect(chatStore.isStreaming.value).toBe(true)
    expect(chatStore.agentPhase.value).toBe('thinking')
    expect(chatStore.approvalEvent.value).toBeNull()
    expect(chatStore.connectionError.value).toBeNull()
  })

  // --- 2. on_session_init captures chat_id ---
  it('on_session_init captures chat_id', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('chat-1')
    getChat().send('hello')

    fakeSse.emit('on_session_init', { chat_id: 'chat-42' } as SessionInitEvent)
    fakeSse.emit('on_assistant', { delta: 'Hi' })
    fakeSse.emit('on_assistant_done', {})
    const assistantMsg = chatStore.messages.value.find(m => m.type === 'assistant')
    expect(assistantMsg).toBeDefined()
    expect(assistantMsg!.chat_id).toBe('chat-42')
  })

  // --- 3. on_reasoning accumulates deltas ---
  it('on_reasoning accumulates deltas and creates reasoning entry', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('think')
    expect(chatStore.reasonings.value).toEqual([])

    fakeSse.emit('on_reasoning', { delta: 'I need' })
    expect(chatStore.reasonings.value).toHaveLength(1)
    expect(chatStore.reasonings.value[0]!.content).toBe('I need')
    expect(chatStore.reasonings.value[0]!.done).toBe(false)

    fakeSse.emit('on_reasoning', { delta: ' to think' })
    expect(chatStore.reasonings.value[0]!.content).toBe('I need to think')
    expect(chatStore.reasonings.value).toHaveLength(1)
  })

  // --- 4. on_thinking_done marks reasoning done ---
  it('on_thinking_done marks reasoning done, phase=responding', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('p')
    fakeSse.emit('on_reasoning', { delta: 'thinking...' })
    fakeSse.emit('on_thinking_done', {})
    expect(chatStore.reasonings.value[0]!.done).toBe(true)
    expect(chatStore.agentPhase.value).toBe('responding')
  })

  // --- 5. on_assistant appends to buffer, no message yet ---
  it('on_assistant appends to buffer without creating message', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('tell me')
    fakeSse.emit('on_assistant', { delta: 'He' })
    fakeSse.emit('on_assistant', { delta: 'llo' })
    expect(chatStore.messages.value.filter(m => m.type === 'assistant')).toHaveLength(0)
  })

  // --- 6. on_assistant_done creates message from buffer ---
  it('on_assistant_done creates assistant message from buffer', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('hey')
    fakeSse.emit('on_assistant', { delta: 'Hel' })
    fakeSse.emit('on_assistant', { delta: 'lo!' })
    fakeSse.emit('on_assistant_done', {})
    const msgs = chatStore.messages.value.filter(m => m.type === 'assistant')
    expect(msgs).toHaveLength(1)
    expect(msgs[0]!.content).toBe('Hello!')
  })

  // --- 7. on_tool_call creates ToolCallInfo ---
  it('on_tool_call creates ToolCallInfo with RUNNING, phase=calling_tool', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('run tool')
    fakeSse.emit('on_tool_call', {
      call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'ls' }, is_read_only: false,
    } as ToolCallEvent)
    expect(chatStore.agentPhase.value).toBe('calling_tool')
    const tc = chatStore.toolCalls.value.get('tc-1')
    expect(tc!.execution_status).toBe('RUNNING')
    expect(tc!.tool_name).toBe('bash')
  })

  // --- 8. on_tool_result updates ToolCallInfo ---
  it('on_tool_result updates tool call, phase=thinking', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('run tool')
    fakeSse.emit('on_tool_call', {
      call_id: 'tc-2', tool_name: 'read', params: {}, is_read_only: true,
    } as ToolCallEvent)
    fakeSse.emit('on_tool_result', {
      call_id: 'tc-2', execution_status: 'SUCCEEDED', output: { content: 'file' }, execution_time_ms: 150,
    } as ToolResultEvent)
    expect(chatStore.agentPhase.value).toBe('thinking')
    const tc = chatStore.toolCalls.value.get('tc-2')
    expect(tc!.execution_status).toBe('SUCCEEDED')
    expect(tc!.output).toEqual({ content: 'file' })
    expect(tc!.execution_time_ms).toBe(150)
  })

  // --- 9. approval flow ---
  it('on_tool_approval_required sets approvalEvent, phase=awaiting_approval', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('delete')
    fakeSse.emit('on_tool_call', {
      call_id: 'tc-3', tool_name: 'rm', params: { path: '/tmp/x' }, is_read_only: false,
    } as ToolCallEvent)
    fakeSse.emit('on_tool_approval_required', {
      chat_id: 'c1', request_id: 'req-1', tool_name: 'rm', params: { path: '/tmp/x' },
      reason: 'dangerous', call_id: 'tc-3',
    } as ToolApprovalRequiredEvent)
    expect(chatStore.agentPhase.value).toBe('awaiting_approval')
    expect(chatStore.approvalEvent.value!.status).toBe('pending')
    const tc = chatStore.toolCalls.value.get('tc-3')
    expect(tc!.execution_status).toBe('PENDING_APPROVAL')
  })

  // --- 11. on_done preserves connectionError set by preceding on_error ---
  it('on_done preserves connectionError from preceding on_error', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('test')
    fakeSse.emit('on_error', { code: 'TIMEOUT', message: 'Server timeout' } as ErrorEvent)
    fakeSse.emit('on_done', {})
    expect(chatStore.connectionError.value!.code).toBe('TIMEOUT')
  })

  // --- 12. new chat: session added at session_init with first-message title ---
  it('new chat adds session to list at session_init with first-message title', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId(null)
    getChat().send('first message')
    fakeSse.emit('on_session_init', { chat_id: 'real-abc' } as SessionInitEvent)
    expect(sessionListStore.activeChatId.value).toBe('real-abc')
    expect(sessionListStore.sessions.value).toHaveLength(1)
    expect(sessionListStore.sessions.value[0]!.chat_id).toBe('real-abc')
    expect(sessionListStore.sessions.value[0]!.title).toBe('first message')
    fakeSse.emit('on_done', {})
    // Still only one session, not duplicated
    expect(sessionListStore.sessions.value).toHaveLength(1)
  })

  // --- 13. on_error sets connectionError ---
  it('on_error sets connectionError, isStreaming=false, phase=idle', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('test')
    fakeSse.emit('on_error', { code: 'SERVER_ERROR', message: 'Boom!' } as ErrorEvent)
    expect(chatStore.isStreaming.value).toBe(false)
    expect(chatStore.agentPhase.value).toBe('idle')
    expect(chatStore.connectionError.value).toEqual({ code: 'SERVER_ERROR', message: 'Boom!' })
  })

  // --- 14. abort ---
  it('abort aborts controller, sets streaming=false, marks reasonings done', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('test')
    fakeSse.emit('on_reasoning', { delta: 'thinking...' })
    getChat().abort()
    expect(chatStore.isStreaming.value).toBe(false)
    expect(chatStore.agentPhase.value).toBe('idle')
    expect(chatStore.reasonings.value[0]!.done).toBe(true)
    expect(fakeSse.isAborted()).toBe(true)
  })

  // --- 15. AbortError silently handled ---
  it('AbortError in connect is silently ignored', async () => {
    const abortFake: SseClient = {
      async connect() { return Promise.reject(new DOMException('Aborted', 'AbortError')) }
    }
    const { getChat } = mountUseChat(abortFake, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('hi')
    await flushMicrotasks()
    expect(chatStore.isStreaming.value).toBe(false)
    expect(chatStore.connectionError.value).toBeNull()
  })

  // --- 16. Network error sets connectionError ---
  it('Network error sets connectionError', async () => {
    const netFake: SseClient = {
      async connect() { return Promise.reject(new Error('Failed to fetch')) }
    }
    const { getChat } = mountUseChat(netFake, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('hi')
    await flushMicrotasks()
    expect(chatStore.connectionError.value!.code).toBe('NETWORK_ERROR')
  })

  // --- 17. 800ms timeout transitions calling_tool -> waiting_for_tool ---
  it('800ms timeout transitions calling_tool to waiting_for_tool', async () => {
    vi.useFakeTimers()
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('run tool')
    fakeSse.emit('on_tool_call', {
      call_id: 'tc-slow', tool_name: 'slow', params: {}, is_read_only: true,
    } as ToolCallEvent)
    expect(chatStore.agentPhase.value).toBe('calling_tool')
    await vi.advanceTimersByTimeAsync(801)
    expect(chatStore.agentPhase.value).toBe('waiting_for_tool')
    vi.useRealTimers()
  })

  // --- 18. on_assistant marks pending reasoning done ---
  it('on_assistant marks pending reasoning done before appending', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('go')
    fakeSse.emit('on_reasoning', { delta: 'hmm' })
    expect(chatStore.reasonings.value[0]!.done).toBe(false)
    fakeSse.emit('on_assistant', { delta: 'Answer:' })
    expect(chatStore.reasonings.value[0]!.done).toBe(true)
    expect(chatStore.agentPhase.value).toBe('responding')
  })

  // --- 19. unknown call_id in tool_result warns ---
  it('on_tool_result with unknown call_id warns but does not crash', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    getChat().setChatId('c1')
    getChat().send('test')
    fakeSse.emit('on_tool_result', {
      call_id: 'nonexistent', execution_status: 'SUCCEEDED',
    } as ToolResultEvent)
    expect(warnSpy).toHaveBeenCalledWith('[SSE] on_tool_result: unknown call_id', 'nonexistent')
    warnSpy.mockRestore()
  })
})
