import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref, nextTick } from 'vue'
import { useAutoScroll } from '@/presentation/composables/use-auto-scroll'

describe('useAutoScroll', () => {
  // Create a minimal mock container with scrollTop, clientHeight, scrollHeight
  function createMockContainer(scrollHeight: number, clientHeight: number): HTMLElement {
    const el = document.createElement('div')
    Object.defineProperty(el, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    })
    Object.defineProperty(el, 'clientHeight', {
      configurable: true,
      get: () => clientHeight,
    })
    let _scrollTop = 0
    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      get: () => _scrollTop,
      set: (v: number) => { _scrollTop = v },
    })
    return el
  }

  it('scrollToBottom sets scrollTop to scrollHeight', () => {
    const container = createMockContainer(1000, 400)
    const containerRef = ref<HTMLElement | null>(container)
    const { scrollToBottom } = useAutoScroll(containerRef, ref(0))
    scrollToBottom()
    // After nextTick, scrollTop should be set
    return nextTick().then(() => {
      expect(container.scrollTop).toBe(1000)
    })
  })

  it('handleScroll detects user scrolled up (more than 50px from bottom)', () => {
    const container = createMockContainer(1000, 400)
    const containerRef = ref<HTMLElement | null>(container)
    const { isScrolledUp, handleScroll } = useAutoScroll(containerRef, ref(0))

    // User at bottom: scrollTop=600 (clientHeight=400, scrollHeight=1000)
    container.scrollTop = 600
    handleScroll()
    expect(isScrolledUp.value).toBe(false) // exactly 0px from bottom

    // User scrolled up 60px from bottom
    container.scrollTop = 540
    handleScroll()
    expect(isScrolledUp.value).toBe(true) // 60px from bottom > 50 threshold

    // User scrolled back to within 50px of bottom
    container.scrollTop = 555
    handleScroll()
    expect(isScrolledUp.value).toBe(false) // 45px from bottom
  })

  it('handleScroll returns early when container is null', () => {
    const containerRef = ref<HTMLElement | null>(null)
    const { handleScroll } = useAutoScroll(containerRef, ref(0))
    // Should not throw
    expect(() => handleScroll()).not.toThrow()
  })

  it('auto-scroll triggers when watchLength changes and user not scrolled up', async () => {
    const container = createMockContainer(1000, 400)
    container.scrollTop = 600 // at bottom
    const containerRef = ref<HTMLElement | null>(container)
    const watchLength = ref(0)

    useAutoScroll(containerRef, watchLength)

    // Change watchLength — should auto-scroll because user is at bottom
    watchLength.value = 5
    await nextTick() // watch fires, scrollToBottom queues nextTick
    await nextTick() // scrollToBottom's nextTick runs
    expect(container.scrollTop).toBe(1000)
  })

  it('does not auto-scroll when user is scrolled up', async () => {
    const container = createMockContainer(1000, 400)
    container.scrollTop = 540 // scrolled up (600 - 540 = 60px from bottom > 50)
    const containerRef = ref<HTMLElement | null>(container)
    const watchLength = ref(0)

    const { isScrolledUp, handleScroll } = useAutoScroll(containerRef, watchLength)

    // Mark as scrolled up
    handleScroll()
    expect(isScrolledUp.value).toBe(true)

    // Record current position
    const currentScrollTop = container.scrollTop

    watchLength.value = 10
    await nextTick() // watch fires, but isScrolledUp blocks scrollToBottom
    await nextTick() // ensure any queued nextTick runs (though none should)
    // Should NOT have scrolled
    expect(container.scrollTop).toBe(currentScrollTop)
  })
})
