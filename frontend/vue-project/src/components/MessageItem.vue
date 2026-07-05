<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import type { Message } from '@/domain/models'
import { renderMarkdown } from '@/infrastructure/markdown'

const props = defineProps<{ message: Message }>()

const contentRef = ref<HTMLElement | null>(null)

const renderedHtml = computed(() => {
  if (props.message.type === 'assistant') {
    return renderMarkdown(props.message.content)
  }
  return ''
})

// Highlight code blocks after each render (highlight.js is loaded lazily by renderMarkdown)
async function highlightCodeBlocks() {
  await nextTick()
  if (!contentRef.value) return
  const codeBlocks = contentRef.value.querySelectorAll('pre code')
  if (codeBlocks.length === 0) return
  try {
    const hljs = (await import('highlight.js')).default
    codeBlocks.forEach(block => hljs.highlightElement(block as HTMLElement))
  } catch {
    // highlight.js not available, code blocks will not be syntax-highlighted
  }
}

watch(renderedHtml, highlightCodeBlocks)
</script>

<template>
  <div v-if="message.type === 'user'" class="d-flex justify-content-end mb-3">
    <div
      class="chat-bubble chat-bubble-user bg-primary text-white rounded-3 px-3 py-2"
      data-testid="user-bubble"
      style="max-width: 75%"
    >
      {{ message.content }}
    </div>
  </div>

  <div v-else-if="message.type === 'assistant' && message.content" class="d-flex mb-3">
    <div
      class="chat-bubble chat-bubble-assistant bg-light rounded-3 px-3 py-2"
      data-testid="assistant-bubble"
      style="max-width: 85%"
    >
      <div ref="contentRef" v-html="renderedHtml"></div>
    </div>
  </div>

  <div v-else-if="message.type === 'system'" class="d-flex justify-content-center mb-2">
    <div
      class="chat-bubble chat-bubble-system bg-secondary bg-opacity-10 text-muted rounded-3 px-3 py-1 small"
      data-testid="system-bubble"
    >
      {{ message.content }}
    </div>
  </div>
</template>
