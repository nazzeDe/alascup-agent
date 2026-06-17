import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { ToastStore } from '@/application/toast-store'
import { SessionListStore } from '@/application/session-list-store'
import { SessionService } from '@/application/session-service'
import { ActiveSessionWorkspace } from '@/application/active-session-workspace'
import { FetchSessionApi } from '@/infrastructure/session-api'
import { FetchApprovalApi } from '@/infrastructure/approval-api'

const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

function makeSessionProviders(sessionListStore: SessionListStore) {
  const sessionService = new SessionService(
    new FetchSessionApi(), new FetchApprovalApi(), sessionListStore
  )
  const activeSessionWorkspace = new ActiveSessionWorkspace(sessionService, sessionListStore)
  return {
    sessionListStore,
    chatStore: activeSessionWorkspace.activeChatStore,
    sessionService,
    activeSessionWorkspace,
  }
}

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
          ...makeSessionProviders(sessionListStore),
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

describe('SessionList loading states', () => {
  it('shows spinner when sessions are loading', async () => {
    const { default: SessionList } = await import('@/components/SessionList.vue')
    const sessionListStore = new SessionListStore()
    sessionListStore.setLoading(true)

    const wrapper = mount(SessionList, {
      global: {
        provide: {
          ...makeSessionProviders(sessionListStore),
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
