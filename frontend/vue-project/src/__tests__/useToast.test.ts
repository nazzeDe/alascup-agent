import { describe, it, expect, beforeEach, vi } from 'vitest'

describe('useToast', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    // Reset module state between tests
    const { useToast } = await import('@/composables/useToast')
    const { toasts } = useToast()
    toasts.value = []
  })

  it('adds a toast with unique id', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()

    showToast('error', 'Something went wrong', 'ERR_001')
    expect(toasts.value).toHaveLength(1)
    expect(toasts.value[0]!.type).toBe('error')
    expect(toasts.value[0]!.message).toBe('Something went wrong')
    expect(toasts.value[0]!.code).toBe('ERR_001')
    expect(toasts.value[0]!.id).toBeDefined()

    showToast('info', 'Another message')
    expect(toasts.value).toHaveLength(2)
    expect(toasts.value[0]!.id).not.toBe(toasts.value[1]!.id)
  })

  it('dismisses toast by id', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast, dismissToast } = useToast()

    showToast('warning', 'Warning msg')
    const id = toasts.value[0]!.id
    dismissToast(id)
    expect(toasts.value).toHaveLength(0)
  })

  it('auto-dismisses after default duration', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()

    showToast('success', 'Done')
    expect(toasts.value).toHaveLength(1)

    vi.advanceTimersByTime(8000)
    expect(toasts.value).toHaveLength(0)
  })

  it('supports custom duration', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()

    showToast('info', 'Quick', undefined, 1000)
    vi.advanceTimersByTime(900)
    expect(toasts.value).toHaveLength(1)
    vi.advanceTimersByTime(200)
    expect(toasts.value).toHaveLength(0)
  })

  it('shares state across composable instances', async () => {
    const { useToast: use1 } = await import('@/composables/useToast')
    const { useToast: use2 } = await import('@/composables/useToast')

    const a = use1()
    const b = use2()

    a.showToast('error', 'Shared')
    expect(b.toasts.value).toHaveLength(1)
    expect(a.toasts.value).toBe(b.toasts.value)
  })

  it('supports four toast types', async () => {
    const { useToast } = await import('@/composables/useToast')
    const { toasts, showToast } = useToast()

    showToast('error', 'e')
    showToast('warning', 'w')
    showToast('info', 'i')
    showToast('success', 's')

    expect(toasts.value).toHaveLength(4)
    expect(toasts.value.map(t => t.type)).toEqual(['error', 'warning', 'info', 'success'])
  })
})
