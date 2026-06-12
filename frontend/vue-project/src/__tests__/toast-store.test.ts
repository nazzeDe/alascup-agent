import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ToastStore } from '@/application/toast-store'

describe('ToastStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('creates independent instances', () => {
    const a = new ToastStore()
    const b = new ToastStore()
    expect(a.toasts).toBeDefined()
    expect(b.toasts).toBeDefined()
    expect(a.toasts).not.toBe(b.toasts)
    expect(a.toasts.value).toEqual([])
    expect(b.toasts.value).toEqual([])
  })

  it('adds a toast with unique id', () => {
    const store = new ToastStore()

    store.show('error', 'Something went wrong', 'ERR_001')
    expect(store.toasts.value).toHaveLength(1)
    expect(store.toasts.value[0]!.type).toBe('error')
    expect(store.toasts.value[0]!.message).toBe('Something went wrong')
    expect(store.toasts.value[0]!.code).toBe('ERR_001')
    expect(store.toasts.value[0]!.id).toBeDefined()
    expect(store.toasts.value[0]!.duration).toBe(8000)

    store.show('info', 'Another message')
    expect(store.toasts.value).toHaveLength(2)
    expect(store.toasts.value[0]!.id).not.toBe(store.toasts.value[1]!.id)
  })

  it('dismisses toast by id', () => {
    const store = new ToastStore()

    store.show('warning', 'Warning msg')
    const id = store.toasts.value[0]!.id
    store.dismiss(id)
    expect(store.toasts.value).toHaveLength(0)
  })

  it('auto-dismisses after default duration', () => {
    const store = new ToastStore()

    store.show('success', 'Done')
    expect(store.toasts.value).toHaveLength(1)

    vi.advanceTimersByTime(8000)
    expect(store.toasts.value).toHaveLength(0)
  })

  it('supports custom duration', () => {
    const store = new ToastStore()

    store.show('info', 'Quick', undefined, 1000)
    vi.advanceTimersByTime(900)
    expect(store.toasts.value).toHaveLength(1)
    vi.advanceTimersByTime(200)
    expect(store.toasts.value).toHaveLength(0)
  })

  it('has independent state across instances', () => {
    const a = new ToastStore()
    const b = new ToastStore()

    a.show('error', 'From A')
    expect(a.toasts.value).toHaveLength(1)
    expect(b.toasts.value).toHaveLength(0)

    b.show('info', 'From B')
    expect(a.toasts.value).toHaveLength(1)
    expect(b.toasts.value).toHaveLength(1)
    expect(a.toasts.value[0]!.message).toBe('From A')
    expect(b.toasts.value[0]!.message).toBe('From B')
  })

  it('handles dismissing non-existent id gracefully', () => {
    const store = new ToastStore()
    store.show('info', 'Only toast')
    store.dismiss('nonexistent')
    expect(store.toasts.value).toHaveLength(1)
  })
})
