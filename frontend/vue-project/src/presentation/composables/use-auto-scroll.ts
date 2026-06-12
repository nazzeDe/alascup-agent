import { ref, watch, nextTick, type Ref } from 'vue'

export function useAutoScroll(
  containerRef: Ref<HTMLElement | null>,
  watchLength: Ref<number>,
) {
  const isScrolledUp = ref(false)

  function scrollToBottom() {
    isScrolledUp.value = false
    nextTick(() => {
      if (containerRef.value) {
        containerRef.value.scrollTop = containerRef.value.scrollHeight
      }
    })
  }

  function handleScroll() {
    if (!containerRef.value) return
    const { scrollTop, clientHeight, scrollHeight } = containerRef.value
    isScrolledUp.value = scrollTop + clientHeight < scrollHeight - 50
  }

  watch(watchLength, () => {
    if (!isScrolledUp.value) scrollToBottom()
  })

  return { isScrolledUp, handleScroll, scrollToBottom }
}
