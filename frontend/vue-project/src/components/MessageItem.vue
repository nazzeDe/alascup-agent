<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Message } from '@/types'

const props = defineProps<{ message: Message }>()

const contentRef = ref<HTMLElement | null>(null)

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node instanceof HTMLAnchorElement) {
    const href = node.getAttribute('href')
    if (href && !/^(https?:|\/|mailto:|#)/.test(href)) {
      node.removeAttribute('href')
    }
  }
})

const renderedHtml = computed(() => {
  if (props.message.type === 'assistant' && !props.message.is_meta) {
    const raw = marked.parse(props.message.content, { async: false }) as string
    return DOMPurify.sanitize(raw)
  }
  return ''
})

let hljsLoaded = false

async function highlightCodeBlocks() {
  await nextTick()
  if (!contentRef.value) return
  const codeBlocks = contentRef.value.querySelectorAll('pre code')
  if (codeBlocks.length === 0) return
  try {
    if (!hljsLoaded) {
      const hljs = (await import('highlight.js/lib/core')).default
      const bash = (await import('highlight.js/lib/languages/bash')).default
      const json = (await import('highlight.js/lib/languages/json')).default
      const python = (await import('highlight.js/lib/languages/python')).default
      hljs.registerLanguage('bash', bash)
      hljs.registerLanguage('json', json)
      hljs.registerLanguage('python', python)
      hljsLoaded = true
    }
    const hljs = (await import('highlight.js/lib/core')).default
    codeBlocks.forEach(block => hljs.highlightElement(block as HTMLElement))
  } catch {
    console.warn('highlight.js not available, code blocks will not be syntax-highlighted')
  }
}

watch(renderedHtml, highlightCodeBlocks)
</script>

<template>
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
