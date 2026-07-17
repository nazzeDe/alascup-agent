import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { FetchEventSourceClient } from '@/infrastructure/sse-client'
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

  it('aborts the active stream before switching or creating sessions', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          chat_id: 'session-1',
          title: 'Session 1',
          messages: [],
          executed_tool_list: [],
          timestamp: new Date().toISOString(),
        },
      ],
    })
    const connectSpy = vi
      .spyOn(FetchEventSourceClient.prototype, 'connect')
      .mockImplementation((_body, _onEvent, signal) => new Promise<void>((resolve) => {
        signal.addEventListener('abort', () => resolve(), { once: true })
      }))
    const { default: App } = await import('@/App.vue')
    const wrapper = mount(App)
    await nextTick()
    await nextTick()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('hello')
    await wrapper.get('[data-testid="send-button"]').trigger('click')
    const firstSignal = connectSpy.mock.calls[0]?.[2]

    await wrapper.get('[data-testid="session-item"]').trigger('click')

    expect(firstSignal?.aborted).toBe(true)

    await textarea.setValue('hello again')
    await wrapper.get('[data-testid="send-button"]').trigger('click')
    const secondSignal = connectSpy.mock.calls[1]?.[2]

    await wrapper.get('[data-testid="new-session-button"]').trigger('click')

    expect(secondSignal?.aborted).toBe(true)
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
  it('keeps evidence details collapsed behind a scannable execution summary', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-summary',
          chat_id: 'chat-1',
          tool_name: 'get_cpu_usage',
          is_read_only: true,
          approval_status: 'APPROVED',
          execution_status: 'SUCCEEDED',
          execution_time_ms: 1500,
          output: 'CPU usage: 42%',
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    expect({
      readWrite: wrapper.get('[data-testid="tool-read-write"]').text(),
      name: wrapper.get('[data-testid="tool-name"]').text(),
      approval: wrapper.get('[data-testid="tool-approval-status"]').text(),
      status: wrapper.get('[data-testid="tool-execution-status"]').text(),
      duration: wrapper.get('[data-testid="tool-duration"]').text(),
      detailsVisible: wrapper.find('[data-testid="tool-evidence-details"]').exists(),
    }).toEqual({
      readWrite: 'R',
      name: 'get_cpu_usage',
      approval: 'Approved',
      status: 'Done',
      duration: '1.5s',
      detailsVisible: false,
    })
  })

  it('reveals complete generic execution evidence when expanded', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-details',
          chat_id: 'chat-1',
          tool_name: 'restart_service',
          server: 'tool-server',
          is_read_only: false,
          is_rollbackable: true,
          params: { service: 'nginx', options: { wait: true } },
          approval_status: 'APPROVED',
          execution_status: 'FAILED',
          output: { attempted: true },
          error: { code: 503, message: 'Service unavailable', data: 'timeout' },
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')

    expect({
      server: wrapper.get('[data-testid="tool-server"]').text(),
      rollback: wrapper.get('[data-testid="tool-rollback"]').text(),
      parameters: wrapper.get('[data-testid="tool-parameters"]').text(),
      result: wrapper.get('[data-testid="tool-result"]').text(),
      error: wrapper.get('[data-testid="tool-error"]').text(),
    }).toEqual({
      server: 'tool-server',
      rollback: 'Rollbackable',
      parameters: JSON.stringify({ service: 'nginx', options: { wait: true } }, null, 2),
      result: JSON.stringify({ attempted: true }, null, 2),
      error: JSON.stringify({ code: 503, message: 'Service unavailable', data: 'timeout' }, null, 2),
    })
  })

  it('omits rollback evidence when the backend did not provide it', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-live',
          chat_id: 'chat-1',
          tool_name: 'get_cpu_usage',
          server: 'tool-server',
          is_read_only: true,
          execution_status: 'SUCCEEDED',
          output: 'CPU usage: 42%',
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')

    expect(wrapper.find('[data-testid="tool-rollback"]').exists()).toBe(false)
  })

  it.each([
    [
      '20 lines',
      Array.from({ length: 21 }, (_, index) => `line ${index + 1}`).join('\n'),
      'line 20',
      'line 21',
    ],
    ['16 KiB', `${'x'.repeat(16 * 1024)}OVER_LIMIT`, 'xxxx', 'OVER_LIMIT'],
  ])('caps the visible result preview at %s', async (_limit, output, included, excluded) => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-preview',
          chat_id: 'chat-1',
          tool_name: 'read_logs',
          is_read_only: true,
          execution_status: 'SUCCEEDED',
          output,
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')
    const preview = wrapper.get('[data-testid="tool-result"]').text()

    expect({
      includesAllowedContent: preview.includes(included),
      excludesOverflow: !preview.includes(excluded),
      showsTruncationNotice: wrapper.find('[data-testid="tool-result-truncated"]').exists(),
    }).toEqual({
      includesAllowedContent: true,
      excludesOverflow: true,
      showsTruncationNotice: true,
    })
  })

  it('copies the complete result when the visible preview is truncated', async () => {
    let copiedText = ''
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (text: string) => { copiedText = text },
      },
    })
    const completeOutput = `${'x'.repeat(16 * 1024)}COPY_THIS_ENDING`
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-copy',
          chat_id: 'chat-1',
          tool_name: 'read_logs',
          is_read_only: true,
          execution_status: 'SUCCEEDED',
          output: completeOutput,
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')
    await wrapper.get('[data-testid="tool-result-copy"]').trigger('click')

    expect(copiedText).toBe(completeOutput)
  })

  it('resets copy feedback when the card receives a different result', async () => {
    let copiedText = ''
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (text: string) => { copiedText = text },
      },
    })
    const firstToolCall = {
      call_id: 'tc-first',
      chat_id: 'chat-1',
      tool_name: 'read_logs',
      is_read_only: true,
      execution_status: 'SUCCEEDED' as const,
      output: 'first result',
      timestamp: '2026-06-17T00:00:00.000Z',
    }
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, { props: { tool_call: firstToolCall } })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')
    await wrapper.get('[data-testid="tool-result-copy"]').trigger('click')
    await wrapper.setProps({
      tool_call: { ...firstToolCall, call_id: 'tc-second', output: 'second result' },
    })
    const feedbackAfterChange = wrapper.get('[data-testid="tool-result-copy"]').text()
    await wrapper.get('[data-testid="tool-result-copy"]').trigger('click')

    expect({ feedbackAfterChange, copiedText }).toEqual({
      feedbackAfterChange: 'Copy result',
      copiedText: 'second result',
    })
  })

  it('presents a null result instead of treating it as missing', async () => {
    const { default: ToolCallInline } = await import('@/components/ToolCallInline.vue')
    const wrapper = mount(ToolCallInline, {
      props: {
        tool_call: {
          call_id: 'tc-null',
          chat_id: 'chat-1',
          tool_name: 'lookup_value',
          is_read_only: true,
          execution_status: 'SUCCEEDED',
          output: null,
          timestamp: '2026-06-17T00:00:00.000Z',
        },
      },
    })

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')

    expect(wrapper.get('[data-testid="tool-result"]').text()).toBe('null')
  })

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

    await wrapper.get('[data-testid="tool-evidence-summary"]').trigger('click')

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
