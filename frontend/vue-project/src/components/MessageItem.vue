<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Message } from '@/types'

const props = defineProps<{ message: Message }>()

const contentRef = ref<HTMLElement | null>(null)

const renderedHtml = computed(() => {
  if (props.message.type === 'assistant' && !props.message.is_meta) {
    return DOMPurify.sanitize(marked.parse(props.message.content) as string)
  }
  return ''
})

// Apply syntax highlighting after DOM update
watch(renderedHtml, async () => {
  await nextTick()
  if (contentRef.value) {
    const codeBlocks = contentRef.value.querySelectorAll('pre code')
    if (codeBlocks.length > 0) {
      try {
        const hljs = (await import('highlight.js/lib/core')).default
        const bash = (await import('highlight.js/lib/languages/bash')).default
        const json = (await import('highlight.js/lib/languages/json')).default
        const python = (await import('highlight.js/lib/languages/python')).default
        hljs.registerLanguage('bash', bash)
        hljs.registerLanguage('json', json)
        hljs.registerLanguage('python', python)
        codeBlocks.forEach(block => hljs.highlightElement(block as HTMLElement))
      } catch {
        // highlight.js not available — gracefully degrade
      }
    }
  }
})
</script>

<template>
  <!-- FE-012: is_meta takes precedence over type -->
  <div v-if="message.is_meta" class="d-flex justify-content-center mb-2">
    <div class="chat-meta text-muted small">
      {{ message.content }}
    </div>
  </div>

  <div v-else-if="message.type === 'user'" class="d-flex justify-content-end mb-3">
    <div class="chat-bubble chat-bubble-user bg-primary text-white rounded-3 px-3 py-2" style="max-width: 75%">
      {{ message.content }}
    </div>
  </div>

  <div v-else-if="message.type === 'assistant'" class="d-flex mb-3">
    <div class="chat-bubble chat-bubble-assistant bg-light rounded-3 px-3 py-2" style="max-width: 85%">
      <div ref="contentRef" v-html="renderedHtml"></div>
    </div>
  </div>

  <div v-else-if="message.type === 'system'" class="d-flex justify-content-center mb-2">
    <div class="chat-bubble chat-bubble-system bg-secondary bg-opacity-10 text-muted rounded-3 px-3 py-1 small">
      {{ message.content }}
    </div>
  </div>
</template>
