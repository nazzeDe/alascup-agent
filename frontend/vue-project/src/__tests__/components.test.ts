import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

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
    expect(wrapper.find('.chat-meta').exists()).toBe(true)
    expect(wrapper.find('.chat-bubble-assistant').exists()).toBe(false)
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
    const btn = wrapper.find('.btn-new-session')
    expect(btn.attributes('disabled')).toBeDefined()
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
