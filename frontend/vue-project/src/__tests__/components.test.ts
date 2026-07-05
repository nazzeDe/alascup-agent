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

describe('ToolCallInline', () => {
  it('renders rejected tool results with the backend rejection message', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-1',
          chat_id: 'chat-1',
          tool_name: 'bash',
          is_read_only: false,
          execution_status: 'REJECTED',
          error: { message: 'Tool was rejected by human. Do NOT retry.' },
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    expect(wrapper.text()).toContain('Rejected')
    expect(wrapper.text()).toContain('Tool was rejected by human. Do NOT retry.')
  })

  it('updates elapsed time when the tool result prop changes', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const toolCall = {
      call_id: 'tc-1',
      chat_id: 'chat-1',
      tool_name: 'bash',
      is_read_only: true,
      execution_status: 'RUNNING' as const,
      timestamp: '2026-06-17T00:00:00.000Z',
    }
    const wrapper = mount(ToolCallInline, {
      props: { tool_call: toolCall },
    })

    expect(wrapper.text()).not.toContain('1.5s')

    await wrapper.setProps({
      tool_call: {
        ...toolCall,
        execution_status: 'SUCCEEDED',
        execution_time_ms: 1500,
      },
    })

    expect(wrapper.text()).toContain('1.5s')
  })

  it('renders zero millisecond execution time', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-1',
          chat_id: 'chat-1',
          tool_name: 'cache_read',
          is_read_only: true,
          execution_status: 'SUCCEEDED',
          execution_time_ms: 0,
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    expect(wrapper.text()).toContain('0.0s')
  })
})

describe('ApprovalInline', () => {
  it('keeps pending controls enabled after emitting so failed requests can be retried', async () => {
    const { default: ApprovalInline } = await import('@/components/ApprovalInline.vue')
    const wrapper = mount(ApprovalInline, {
      props: {
        event: {
          request_id: 'req-1',
          tool_name: 'bash',
          params: {},
          reason: 'needs approval',
          status: 'pending',
          message: '',
        },
      },
    })

    await wrapper.get('[data-testid="approval-approve-button"]').trigger('click')

    expect(wrapper.emitted('approve')).toHaveLength(1)
    expect(wrapper.get('[data-testid="approval-approve-button"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="approval-reject-button"]').attributes('disabled')).toBeUndefined()
  })

  it('resets the reason input when a new approval event is rendered in the same component', async () => {
    const { default: ApprovalInline } = await import('@/components/ApprovalInline.vue')
    const wrapper = mount(ApprovalInline, {
      props: {
        event: {
          request_id: 'req-1',
          tool_name: 'bash',
          params: {},
          reason: 'needs approval',
          status: 'pending',
          message: 'old reason',
        },
      },
    })

    await wrapper.get('[data-testid="approval-reason-input"]').setValue('typed reason')
    await wrapper.setProps({
      event: {
        request_id: 'req-2',
        tool_name: 'restart',
        params: {},
        reason: 'needs approval',
        status: 'pending',
        message: '',
      },
    })

    expect((wrapper.get('[data-testid="approval-reason-input"]').element as HTMLInputElement).value).toBe('')
  })
})
