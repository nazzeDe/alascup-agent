<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ReasoningEntry } from '@/composables/useChat'

const props = defineProps<{ reasoning: ReasoningEntry }>()

const expanded = ref(false)
const isStreaming = computed(() => !props.reasoning.done)

const label = computed(() => {
  if (isStreaming.value) return 'Thinking…'
  const len = props.reasoning.content.length
  if (len < 50) return `Thought (${len}c)`
  return `Thought (${len}c) — "${props.reasoning.content.slice(0, 40)}…"`
})
</script>

<template>
  <div class="reasoning-bubble my-1" style="max-width: 520px">
    <div
      class="reasoning-toggle d-flex align-items-center small text-muted"
      role="button"
      @click="expanded = !expanded"
      style="cursor: pointer; user-select: none"
    >
      <span class="me-1">{{ expanded ? '▼' : '▶' }}</span>
      <span v-if="isStreaming" class="spinner-border spinner-border-sm me-1" style="width: 10px; height: 10px"></span>
      <span>{{ label }}</span>
    </div>
    <div v-if="expanded" class="reasoning-content small text-secondary mt-1 ps-3 border-start">
      {{ reasoning.content }}
      <span v-if="isStreaming" class="cursor-blink">|</span>
    </div>
  </div>
</template>

<style scoped>
.cursor-blink {
  animation: blink 0.7s infinite;
  color: #6c757d;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
