<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ReasoningEntry } from '@/composables/useSessionManager'

const props = defineProps<{ reasoning: ReasoningEntry }>()

const expanded = ref(false)
const isStreaming = computed(() => !props.reasoning.done)

const charCount = computed(() => props.reasoning.content.length)

const label = computed(() => {
  if (isStreaming.value) return 'Thinking…'
  const len = charCount.value
  if (len < 50) return `Thought (${len}c)`
  return `Thought (${len}c) — "${props.reasoning.content.slice(0, 50)}…"`
})
</script>

<template>
  <div class="reasoning-bubble my-1" style="max-width: 520px">
    <div
      class="reasoning-toggle d-flex align-items-center small"
      role="button"
      style="color: #4b5563; cursor: pointer; user-select: none"
      @click="expanded = !expanded"
    >
      <span class="me-1">{{ expanded ? '▼' : '▶' }}</span>
      <span v-if="isStreaming" class="pulse-dot me-1" style="width:6px;height:6px"></span>
      <span :class="{ shimmer: isStreaming }">{{ label }}</span>
    </div>
    <div v-if="expanded" class="reasoning-content small text-secondary mt-1 ps-3 border-start">
      <span :class="{ shimmer: isStreaming }">{{ reasoning.content }}</span>
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
