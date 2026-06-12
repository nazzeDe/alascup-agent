import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import { ToastStore } from '@/application/toast-store'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { SessionService } from '@/application/session-service'
import { FetchSessionApi } from '@/infrastructure/session-api'
import { FetchApprovalApi } from '@/infrastructure/approval-api'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

describe('SessionList', () => {
  it('renders session items when injected with SessionListStore', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const sessionListStore = new SessionListStore()
    sessionListStore.setSessions([
      { chat_id: 'c1', title: 'Session 1', messages: [], executed_tool_list: [], timestamp: new Date().toISOString() },
      { chat_id: 'c2', title: 'Session 2', messages: [], executed_tool_list: [], timestamp: new Date().toISOString() },
    ])
    sessionListStore.setActive('c1')

    const wrapper = mount(SessionList, {
      global: {
        provide: {
          sessionListStore,
          chatStore: ref(new ChatStore()),
          sessionService: new SessionService(
            new FetchSessionApi(), new FetchApprovalApi(), sessionListStore
          ),
        },
      },
    })
    expect(wrapper.text()).toContain('Session 1')
    expect(wrapper.text()).toContain('Session 2')

    // active_chat_id reactivity: active session gets 'active' class
    const activeItem = wrapper.find('.session-item.active')
    expect(activeItem.exists()).toBe(true)
    expect(activeItem.text()).toContain('Session 1')
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
  it('shows spinner when sessions are loading', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const sessionListStore = new SessionListStore()
    sessionListStore.setLoading(true)

    const wrapper = mount(SessionList, {
      global: {
        provide: {
          sessionListStore,
          chatStore: ref(new ChatStore()),
          sessionService: new SessionService(
            new FetchSessionApi(), new FetchApprovalApi(), sessionListStore
          ),
        },
      },
    })
    expect(wrapper.text()).toContain('Loading sessions')
  })
})

describe('ToastContainer', () => {
  it('renders toast items from useToast composable', async () => {
    const toastStore = new ToastStore()
    toastStore.show('error', 'Test error')

    const { default: ToastContainer } = await import('@/components/ToastContainer.vue')
    const wrapper = mount(ToastContainer, {
      global: {
        provide: {
          toastStore,
        },
      },
    })
    expect(wrapper.text()).toContain('Test error')
    expect(wrapper.find('.toast').exists()).toBe(true)
  })

  it('renders multiple toasts', async () => {
    const toastStore = new ToastStore()
    toastStore.show('error', 'First')
    toastStore.show('success', 'Second')

    const { default: ToastContainer } = await import('@/components/ToastContainer.vue')
    const wrapper = mount(ToastContainer, {
      global: {
        provide: {
          toastStore,
        },
      },
    })
    const items = wrapper.findAll('.toast')
    expect(items).toHaveLength(2)
  })
})
