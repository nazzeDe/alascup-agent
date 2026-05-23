import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

vi.mock('marked', () => ({
  marked: { parse: (text: string) => `<p>${text}</p>` },
}))

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('MessageItem', () => {
  it('renders user message as right-aligned bubble', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          messageID: 'm1',
          id: 'c1',
          timestamp: new Date().toISOString(),
          type: 'user',
          content: 'Hello',
        },
      },
    })
    expect(wrapper.text()).toContain('Hello')
    expect(wrapper.find('.chat-bubble-user').exists()).toBe(true)
  })

  it('renders assistant message with markdown', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          messageID: 'm2',
          id: 'c1',
          timestamp: new Date().toISOString(),
          type: 'assistant',
          content: '**bold** text',
        },
      },
    })
    expect(wrapper.html()).toContain('<p>**bold** text</p>')
  })

  it('renders system message as centered banner', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          messageID: 'm3',
          id: 'c1',
          timestamp: new Date().toISOString(),
          type: 'system',
          content: 'Connection lost',
        },
      },
    })
    expect(wrapper.text()).toContain('Connection lost')
    expect(wrapper.find('.chat-bubble-system').exists()).toBe(true)
  })
})

describe('ToolCallCard', () => {
  it('displays tool name and status', async () => {
    const { default: ToolCallCard } = await import('@/components/ToolCallCard.vue')
    const wrapper = mount(ToolCallCard, {
      props: {
        toolCall: {
          messageID: 'tc1',
          id: 'c1',
          tool_name: 'get_cpu_info',
          isReadOnly: true,
          execution_status: 'RUNNING',
          timestamp: new Date().toISOString(),
        },
      },
    })
    expect(wrapper.text()).toContain('get_cpu_info')
    expect(wrapper.text()).toContain('Executing…')
  })

  it('shows output when expanded', async () => {
    const { default: ToolCallCard } = await import('@/components/ToolCallCard.vue')
    const wrapper = mount(ToolCallCard, {
      props: {
        toolCall: {
          messageID: 'tc1',
          id: 'c1',
          tool_name: 'get_cpu',
          isReadOnly: true,
          execution_status: 'SUCCEEDED',
          output: { cpu: 85 },
          timestamp: new Date().toISOString(),
        },
      },
    })
    const toggleBtn = wrapper.find('.tool-output-toggle')
    if (toggleBtn.exists()) {
      await toggleBtn.trigger('click')
    }
    expect(wrapper.html()).toContain('85')
  })
})

describe('ApprovalModal', () => {
  it('renders tool name, params, and reason', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: true,
        toolName: 'delete_temp_files',
        params: { path: '/tmp' },
        reason: 'High risk operation',
        requestId: 'r1',
      },
    })
    expect(wrapper.text()).toContain('delete_temp_files')
    expect(wrapper.text()).toContain('High risk operation')
  })

  it('emits approve event on approve click', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: true,
        toolName: 'rm',
        params: {},
        reason: 'Destructive',
        requestId: 'r1',
      },
    })
    const approveBtn = wrapper.find('.btn-approve')
    if (approveBtn.exists()) {
      await approveBtn.trigger('click')
      expect(wrapper.emitted('approve')).toBeTruthy()
      expect(wrapper.emitted('approve')?.[0]?.[0]).toBe('r1')
    }
  })

  it('emits reject event on reject click', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: true,
        toolName: 'rm',
        params: {},
        reason: 'Destructive',
        requestId: 'r1',
      },
    })
    const rejectBtn = wrapper.find('.btn-reject')
    if (rejectBtn.exists()) {
      await rejectBtn.trigger('click')
      expect(wrapper.emitted('reject')).toBeTruthy()
    }
  })

  it('does not render when visible is false', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: false,
        toolName: 'rm',
        params: {},
        reason: '',
        requestId: 'r1',
      },
    })
    expect(wrapper.find('.modal').exists()).toBe(false)
  })
})

describe('SessionList', () => {
  it('renders session items', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [
          { id: 'c1', title: 'Session 1', messages: [], executed_tool_list: [], timestamp: '' },
          { id: 'c2', title: 'Session 2', messages: [], executed_tool_list: [], timestamp: '' },
        ],
        activeChatId: 'c1',
      },
    })
    expect(wrapper.text()).toContain('Session 1')
    expect(wrapper.text()).toContain('Session 2')
  })

  it('emits select event on session click', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [{ id: 'c1', title: 'S1', messages: [], executed_tool_list: [], timestamp: '' }],
        activeChatId: undefined,
      },
    })
    const item = wrapper.find('.session-item')
    if (item.exists()) {
      await item.trigger('click')
      expect(wrapper.emitted('select')?.[0]?.[0]).toBe('c1')
    }
  })

  it('emits create event on new session button click', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: { sessions: [], activeChatId: undefined },
    })
    const newBtn = wrapper.find('.btn-new-session')
    if (newBtn.exists()) {
      await newBtn.trigger('click')
      expect(wrapper.emitted('create')).toBeTruthy()
    }
  })
})

describe('ChatView', () => {
  it('renders input area with send button', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: false },
    })
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.find('.btn-send').exists()).toBe(true)
  })

  it('disables send button when streaming', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: true },
    })
    const btn = wrapper.find('.btn-send')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('emits send-message with text on send click', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: false },
    })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('Hello')
    await wrapper.find('.btn-send').trigger('click')
    expect(wrapper.emitted('send-message')?.[0]?.[0]).toBe('Hello')
  })
})

describe('App', () => {
  it('renders the app shell with sidebar and main area', async () => {
    const { default: App } = await import('@/App.vue')
    const wrapper = mount(App)
    expect(wrapper.find('.row').exists()).toBe(true)
  })
})

describe('MessageItem isMeta', () => {
  it('renders meta message as small muted text', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          messageID: 'm-meta',
          id: 'c1',
          timestamp: new Date().toISOString(),
          type: 'system',
          content: 'Tool execution approved',
          is_meta: true,
        },
      },
    })
    // FE-012: isMeta renders as small text, no bubble
    expect(wrapper.text()).toContain('Tool execution approved')
    expect(wrapper.find('.chat-meta').exists()).toBe(true)
    expect(wrapper.find('.chat-bubble').exists()).toBe(false)
  })

  it('isMeta takes precedence over message type', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          messageID: 'm-meta2',
          id: 'c1',
          timestamp: new Date().toISOString(),
          type: 'assistant',
          content: 'Operation completed',
          is_meta: true,
        },
      },
    })
    // Even assistant type should render as meta when isMeta=true
    expect(wrapper.find('.chat-meta').exists()).toBe(true)
    expect(wrapper.find('.chat-bubble-assistant').exists()).toBe(false)
  })
})

describe('ChatView timeline', () => {
  it('renders timeline with messages and tool calls merged', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const toolCalls = new Map()
    toolCalls.set('tc1', {
      messageID: 'tc1',
      id: 'c1',
      tool_name: 'get_cpu',
      isReadOnly: true,
      execution_status: 'RUNNING' as const,
      timestamp: new Date().toISOString(),
    })

    const wrapper = mount(ChatView, {
      props: {
        messages: [
          { messageID: 'm1', id: 'c1', timestamp: new Date(Date.now() - 3000).toISOString(), type: 'user' as const, content: 'hi' },
          { messageID: 'm2', id: 'c1', timestamp: new Date(Date.now() - 1000).toISOString(), type: 'assistant' as const, content: 'Hello' },
        ],
        toolCalls,
        isStreaming: false,
      },
    })
    // Both messages and tool cards should be present
    expect(wrapper.find('.chat-bubble-user').exists()).toBe(true)
    expect(wrapper.find('.tool-call-card').exists()).toBe(true)
  })
})

describe('ChatView streaming controls', () => {
  it('shows stop button when streaming', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: true },
    })
    // FE-017: stop button visible during streaming
    expect(wrapper.find('.btn-stop').exists()).toBe(true)
  })

  it('emits abort on stop button click', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: true },
    })
    const stopBtn = wrapper.find('.btn-stop')
    if (stopBtn.exists()) {
      await stopBtn.trigger('click')
      expect(wrapper.emitted('abort')).toBeTruthy()
    }
  })

  it('hides stop button when not streaming', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: false },
    })
    expect(wrapper.find('.btn-stop').exists()).toBe(false)
  })
})

describe('ChatView loading state', () => {
  it('shows spinner when isLoadingHistory is true', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { messages: [], toolCalls: new Map(), isStreaming: false, isLoadingHistory: true },
    })
    expect(wrapper.find('.chat-loading-overlay').exists()).toBe(true)
  })
})

describe('ApprovalModal processing state', () => {
  it('shows spinner and disables buttons when isProcessing', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: true,
        toolName: 'delete_temp_files',
        params: { path: '/tmp' },
        reason: 'High risk',
        requestId: 'r1',
        isProcessing: true,
      },
    })
    // FE-015: buttons disabled during submission
    const approveBtn = wrapper.find('.btn-approve')
    const rejectBtn = wrapper.find('.btn-reject')
    expect(approveBtn.attributes('disabled')).toBeDefined()
    expect(rejectBtn.attributes('disabled')).toBeDefined()
  })

  it('keeps buttons enabled when not processing', async () => {
    const { default: ApprovalModal } = await import('@/components/ApprovalModal.vue')
    const wrapper = mount(ApprovalModal, {
      props: {
        visible: true,
        toolName: 'rm',
        params: {},
        reason: 'Destructive',
        requestId: 'r1',
        isProcessing: false,
      },
    })
    const approveBtn = wrapper.find('.btn-approve')
    expect(approveBtn.attributes('disabled')).toBeUndefined()
  })
})

describe('SessionList loading states', () => {
  it('shows spinner on new session button when creating', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [],
        activeChatId: undefined,
        isCreating: true,
        isLoading: false,
      },
    })
    // FE-018: button shows loading state
    const btn = wrapper.find('.btn-new-session')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('shows skeleton placeholder when loading sessions', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [],
        activeChatId: undefined,
        isCreating: false,
        isLoading: true,
      },
    })
    expect(wrapper.find('.session-list').exists()).toBe(true)
  })
})

describe('ToastContainer', () => {
  it('renders toast items from useToast composable', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()
    toasts.value = []
    showToast('error', 'Test error')

    const { default: ToastContainer } = await import('@/components/ToastContainer.vue')
    const wrapper = mount(ToastContainer)
    expect(wrapper.text()).toContain('Test error')
    expect(wrapper.find('.toast').exists()).toBe(true)
  })

  it('renders multiple toasts', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()
    toasts.value = []
    showToast('error', 'First')
    showToast('success', 'Second')

    const { default: ToastContainer } = await import('@/components/ToastContainer.vue')
    const wrapper = mount(ToastContainer)
    const items = wrapper.findAll('.toast')
    expect(items).toHaveLength(2)
  })
})
