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
import type { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import type { SseClient } from '@/application/ports'
import { useChat } from '@/presentation/composables/use-chat'
import type {
  ChatStreamEvent,
} from '@/domain/sse-events'

// FakeSseClient captures the stream event sink so tests can drive SSE events manually.
class FakeSseClient implements SseClient {
  private _onEvent: ((event: ChatStreamEvent) => void) | null = null
  private _signal: AbortSignal | null = null
  private _connectPromise: Promise<void> | null = null
  private _resolveConnect: (() => void) | null = null

  async connect(
    _body: Record<string, unknown>,
    onEvent: (event: ChatStreamEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    this._onEvent = onEvent
    this._signal = signal
    this._connectPromise = new Promise<void>((resolve) => {
      this._resolveConnect = resolve
    })
    return this._connectPromise
  }

  resolveConnect(): void {
    this._resolveConnect?.()
  }

  emit(event: ChatStreamEvent): void {
    this._onEvent?.(event)
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
  const activeSessionWorkspace: Pick<ActiveSessionWorkspace, 'adoptServerSession'> = {
    adoptServerSession(chatId: string, title: string) {
      store.chatId.value = chatId
      sessionListStore.addSession({
        chat_id: chatId,
        title,
        messages: [],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      })
      sessionListStore.setActive(chatId)
    },
  }

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
      provide('activeSessionWorkspace', activeSessionWorkspace)
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
    chatStore.chatId.value = 'chat-1'
    getChat().send('hello')

    expect(chatStore.messages.value).toHaveLength(1)
    expect(chatStore.messages.value[0]!.type).toBe('user')
    expect(chatStore.messages.value[0]!.content).toBe('hello')
    expect(chatStore.isStreaming.value).toBe(true)
    expect(chatStore.agentPhase.value).toBe('thinking')
    expect(chatStore.approvalEvent.value).toBeNull()
    expect(chatStore.connectionError.value).toBeNull()
  })

  it('session_init captures chat_id', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'chat-1'
    getChat().send('hello')

    fakeSse.emit({ type: 'session_init', data: { chat_id: 'chat-42' } })
    fakeSse.emit({ type: 'assistant', data: { delta: 'Hi' } })
    fakeSse.emit({ type: 'assistant_done', data: {} })
    const assistantMsg = chatStore.messages.value.find(m => m.type === 'assistant')
    expect(assistantMsg).toBeDefined()
    expect(assistantMsg!.chat_id).toBe('chat-42')
  })

  it('reasoning accumulates deltas and creates reasoning entry', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('think')
    expect(chatStore.reasonings.value).toEqual([])

    fakeSse.emit({ type: 'reasoning', data: { delta: 'I need' } })
    expect(chatStore.reasonings.value).toHaveLength(1)
    expect(chatStore.reasonings.value[0]!.content).toBe('I need')
    expect(chatStore.reasonings.value[0]!.done).toBe(false)

    fakeSse.emit({ type: 'reasoning', data: { delta: ' to think' } })
    expect(chatStore.reasonings.value[0]!.content).toBe('I need to think')
    expect(chatStore.reasonings.value).toHaveLength(1)
  })

  it('thinking_done marks reasoning done, phase=responding', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('p')
    fakeSse.emit({ type: 'reasoning', data: { delta: 'thinking...' } })
    fakeSse.emit({ type: 'thinking_done', data: {} })
    expect(chatStore.reasonings.value[0]!.done).toBe(true)
    expect(chatStore.agentPhase.value).toBe('responding')
  })

  it('assistant streams into an active assistant message', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('tell me')
    fakeSse.emit({ type: 'assistant', data: { delta: 'He' } })
    fakeSse.emit({ type: 'assistant', data: { delta: 'llo' } })
    const msgs = chatStore.messages.value.filter(m => m.type === 'assistant')
    expect(msgs).toHaveLength(1)
    expect(msgs[0]!.content).toBe('Hello')
  })

  it('assistant_done closes the active assistant message', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('hey')
    fakeSse.emit({ type: 'assistant', data: { delta: 'Hel' } })
    fakeSse.emit({ type: 'assistant', data: { delta: 'lo!' } })
    fakeSse.emit({ type: 'assistant_done', data: {} })
    const msgs = chatStore.messages.value.filter(m => m.type === 'assistant')
    expect(msgs).toHaveLength(1)
    expect(msgs[0]!.content).toBe('Hello!')
  })

  it('tool_call creates ToolCallInfo with RUNNING, phase=calling_tool', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('run tool')
    fakeSse.emit({ type: 'tool_call', data: {
      call_id: 'tc-1', tool_name: 'bash', params: { cmd: 'ls' }, is_read_only: false,
    } })
    expect(chatStore.agentPhase.value).toBe('calling_tool')
    const tc = chatStore.toolCalls.value.get('tc-1')
    expect(tc!.execution_status).toBe('RUNNING')
    expect(tc!.tool_name).toBe('bash')
  })

  it('tool_result updates tool call, phase=thinking', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('run tool')
    fakeSse.emit({ type: 'tool_call', data: {
      call_id: 'tc-2', tool_name: 'read', params: {}, is_read_only: true,
    } })
    fakeSse.emit({ type: 'tool_result', data: {
      call_id: 'tc-2', execution_status: 'SUCCEEDED', output: { content: 'file' }, execution_time_ms: 150,
    } })
    expect(chatStore.agentPhase.value).toBe('thinking')
    const tc = chatStore.toolCalls.value.get('tc-2')
    expect(tc!.execution_status).toBe('SUCCEEDED')
    expect(tc!.output).toEqual({ content: 'file' })
    expect(tc!.execution_time_ms).toBe(150)
  })

  it('tool_approval_required sets approvalEvent, phase=awaiting_approval', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('delete')
    fakeSse.emit({ type: 'tool_call', data: {
      call_id: 'tc-3', tool_name: 'rm', params: { path: '/tmp/x' }, is_read_only: false,
    } })
    fakeSse.emit({ type: 'tool_approval_required', data: {
      chat_id: 'c1', request_id: 'req-1', tool_name: 'rm', params: { path: '/tmp/x' },
      reason: 'dangerous', call_id: 'tc-3',
    } })
    expect(chatStore.agentPhase.value).toBe('awaiting_approval')
    expect(chatStore.approvalEvent.value!.status).toBe('pending')
    const tc = chatStore.toolCalls.value.get('tc-3')
    expect(tc!.execution_status).toBe('PENDING_APPROVAL')
  })

  it('done preserves connectionError from preceding error', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('test')
    fakeSse.emit({ type: 'error', data: { code: 'TIMEOUT', message: 'Server timeout' } })
    fakeSse.emit({ type: 'done', data: {} })
    expect(chatStore.connectionError.value!.code).toBe('TIMEOUT')
  })

  // --- 12. new chat: session added at session_init with first-message title ---
  it('new chat adds session to list at session_init with first-message title', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = null
    getChat().send('first message')
    fakeSse.emit({ type: 'session_init', data: { chat_id: 'real-abc' } })
    expect(sessionListStore.activeChatId.value).toBe('real-abc')
    expect(sessionListStore.sessions.value).toHaveLength(1)
    expect(sessionListStore.sessions.value[0]!.chat_id).toBe('real-abc')
    expect(sessionListStore.sessions.value[0]!.title).toBe('first message')
    fakeSse.emit({ type: 'done', data: {} })
    // Still only one session, not duplicated
    expect(sessionListStore.sessions.value).toHaveLength(1)
  })

  it('error sets connectionError, isStreaming=false, phase=idle', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('test')
    fakeSse.emit({ type: 'error', data: { code: 'SERVER_ERROR', message: 'Boom!' } })
    expect(chatStore.isStreaming.value).toBe(false)
    expect(chatStore.agentPhase.value).toBe('idle')
    expect(chatStore.connectionError.value).toEqual({ code: 'SERVER_ERROR', message: 'Boom!' })
  })

  // --- 14. abort ---
  it('abort aborts controller, sets streaming=false, marks reasonings done', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('test')
    fakeSse.emit({ type: 'reasoning', data: { delta: 'thinking...' } })
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
    chatStore.chatId.value = 'c1'
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
    chatStore.chatId.value = 'c1'
    getChat().send('hi')
    await flushMicrotasks()
    expect(chatStore.connectionError.value!.code).toBe('NETWORK_ERROR')
  })

  // --- 17. 800ms timeout transitions calling_tool -> waiting_for_tool ---
  it('800ms timeout transitions calling_tool to waiting_for_tool', async () => {
    vi.useFakeTimers()
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('run tool')
    fakeSse.emit({ type: 'tool_call', data: {
      call_id: 'tc-slow', tool_name: 'slow', params: {}, is_read_only: true,
    } })
    expect(chatStore.agentPhase.value).toBe('calling_tool')
    await vi.advanceTimersByTimeAsync(801)
    expect(chatStore.agentPhase.value).toBe('waiting_for_tool')
    vi.useRealTimers()
  })

  it('assistant marks pending reasoning done before appending', () => {
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('go')
    fakeSse.emit({ type: 'reasoning', data: { delta: 'hmm' } })
    expect(chatStore.reasonings.value[0]!.done).toBe(false)
    fakeSse.emit({ type: 'assistant', data: { delta: 'Answer:' } })
    expect(chatStore.reasonings.value[0]!.done).toBe(true)
    expect(chatStore.agentPhase.value).toBe('responding')
  })

  it('tool_result with unknown call_id warns but does not crash', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { getChat } = mountUseChat(fakeSse, chatStore, sessionListStore)
    chatStore.chatId.value = 'c1'
    getChat().send('test')
    fakeSse.emit({ type: 'tool_result', data: {
      call_id: 'nonexistent', execution_status: 'SUCCEEDED',
    } })
    expect(warnSpy).toHaveBeenCalledWith('[SSE] tool_result: unknown call_id', 'nonexistent')
    warnSpy.mockRestore()
  })
})
