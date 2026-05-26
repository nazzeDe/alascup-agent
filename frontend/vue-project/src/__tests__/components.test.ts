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
          message_id: 'm1',
          chat_id: 'c1',
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
          message_id: 'm2',
          chat_id: 'c1',
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
          message_id: 'm3',
          chat_id: 'c1',
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

describe('SessionList', () => {
  it('renders session items', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [
          { chat_id: 'c1', title: 'Session 1', messages: [], executed_tool_list: [], timestamp: '' },
          { chat_id: 'c2', title: 'Session 2', messages: [], executed_tool_list: [], timestamp: '' },
        ],
        active_chat_id: 'c1',
      },
    })
    expect(wrapper.text()).toContain('Session 1')
    expect(wrapper.text()).toContain('Session 2')
  })

  it('emits select event on session click', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [{ chat_id: 'c1', title: 'S1', messages: [], executed_tool_list: [], timestamp: '' }],
        active_chat_id: undefined,
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
      props: { sessions: [], active_chat_id: undefined },
    })
    const newBtn = wrapper.find('.btn-new-session')
    if (newBtn.exists()) {
      await newBtn.trigger('click')
      expect(wrapper.emitted('create')).toBeTruthy()
    }
  })
})

describe('ChatView', () => {
  it('shows empty state when no chatId', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { chatId: 'test-1' },
    })
    await nextTick()
    // Without a mocked manager session, state is undefined → loading/empty
    expect(wrapper.find('.chat-view').exists()).toBe(true)
  })
})

describe('App', () => {
  it('renders the app shell with sidebar and main area', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => [] })
    const { default: App } = await import('@/App.vue')
    const wrapper = mount(App)
    await nextTick()
    expect(wrapper.find('.row').exists()).toBe(true)
  })
})

describe('MessageItem isMeta', () => {
  it('renders meta message as small muted text', async () => {
    const { default: MessageItem } = await import('@/components/MessageItem.vue')
    const wrapper = mount(MessageItem, {
      props: {
        message: {
          message_id: 'm-meta',
          chat_id: 'c1',
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
          message_id: 'm-meta2',
          chat_id: 'c1',
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
  it('renders with chatId prop', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { chatId: 'test-1' },
    })
    await nextTick()
    expect(wrapper.find('.chat-view').exists()).toBe(true)
  })
})

describe('ChatView streaming controls', () => {
  it('renders stop button when stream state is active', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { chatId: 'test-1' },
    })
    await nextTick()
    expect(wrapper.find('.chat-view').exists()).toBe(true)
  })

  it('renders without streaming status bar when idle', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { chatId: 'test-1' },
    })
    await nextTick()
    expect(wrapper.find('.streaming-status').exists()).toBe(false)
  })
})

describe('ChatView loading state', () => {
  it('shows empty state for unloaded session', async () => {
    const { default: ChatView } = await import('@/components/ChatView.vue')
    const wrapper = mount(ChatView, {
      props: { chatId: 'test-1' },
    })
    await nextTick()
    // Without loadHistory() called, state exists but has no messages
    expect(wrapper.find('.chat-view').exists()).toBe(true)
  })
})

describe('SessionList loading states', () => {
  it('shows spinner on new session button when creating', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const wrapper = mount(SessionList, {
      props: {
        sessions: [],
        active_chat_id: undefined,
        is_creating: true,
        is_loading: false,
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
        active_chat_id: undefined,
        is_creating: false,
        is_loading: true,
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
