<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { Message, ToolCallInfo } from '@/types'
import MessageItem from './MessageItem.vue'
import ToolCallCard from './ToolCallCard.vue'

const props = defineProps<{
  messages: Message[]
  toolCalls: Map<string, ToolCallInfo>
  isStreaming: boolean
  isLoadingHistory?: boolean
}>()

const emit = defineEmits<{
  'send-message': [text: string]
  abort: []
}>()

const inputText = ref('')
const messagesContainer = ref<HTMLElement | null>(null)
const userScrolledUp = ref(false)

// FE-013: timeline merges messages and tool cards by timestamp
const timeline = computed(() => {
  type TimelineItem =
    | { type: 'message'; data: Message; ts: number }
    | { type: 'tool_card'; data: ToolCallInfo; ts: number }

  const items: TimelineItem[] = []

  for (const m of props.messages) {
    items.push({ type: 'message', data: m, ts: new Date(m.timestamp).getTime() })
  }
  props.toolCalls.forEach((tc) => {
    items.push({ type: 'tool_card', data: tc, ts: new Date(tc.timestamp).getTime() })
  })

  items.sort((a, b) => a.ts - b.ts)
  return items
})

// Auto-scroll on content changes, unless user has scrolled up
function checkAutoScroll() {
  if (userScrolledUp.value) return
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

// Watch for new messages or content changes during streaming
watch(
  () => props.messages.map(m => m.content).join('') + '|' + props.messages.length + '|' + props.toolCalls.size,
  checkAutoScroll,
)

// Track user scroll position
function onScroll() {
  if (!messagesContainer.value) return
  const { scrollTop, clientHeight, scrollHeight } = messagesContainer.value
  userScrolledUp.value = scrollTop + clientHeight < scrollHeight - 50
}

function send() {
  const text = inputText.value.trim()
  if (!text || props.isStreaming) return
  emit('send-message', text)
  inputText.value = ''
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="chat-view d-flex flex-column h-100">
    <div
      ref="messagesContainer"
      class="chat-messages flex-grow-1 overflow-auto p-3"
      @scroll="onScroll"
    >
      <!-- Loading overlay -->
      <div v-if="isLoadingHistory" class="chat-loading-overlay d-flex justify-content-center align-items-center position-absolute top-0 start-0 w-100 h-100" style="z-index: 10; background: rgba(255,255,255,0.7)">
        <div class="spinner-border text-primary" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
      </div>

      <!-- FE-013: timeline renders messages and tool cards in chronological order -->
      <template v-for="item in timeline" :key="item.type === 'message' ? item.data.messageID : item.data.messageID">
        <MessageItem v-if="item.type === 'message'" :message="item.data" />
        <ToolCallCard v-else :tool-call="item.data" />
      </template>

      <div v-if="timeline.length === 0 && !isLoadingHistory" class="text-center text-muted mt-5">
        <p class="fs-4">Alascup Agent</p>
        <p>Start a conversation — ask about system status, diagnostics, or operations.</p>
      </div>
    </div>

    <!-- Streaming status bar -->
    <div v-if="isStreaming" class="streaming-status d-flex align-items-center px-3 py-1 border-top bg-light">
      <div class="spinner-border spinner-border-sm text-primary me-2" role="status">
        <span class="visually-hidden">Loading...</span>
      </div>
      <span class="small text-muted flex-grow-1">AI is responding...</span>
      <button class="btn btn-outline-danger btn-sm btn-stop" @click="emit('abort')">Stop</button>
    </div>

    <div class="chat-input border-top p-2">
      <div class="input-group">
        <textarea
          v-model="inputText"
          class="form-control"
          rows="2"
          placeholder="Type your message... (Enter to send, Shift+Enter for newline)"
          :disabled="isStreaming"
          @keydown="onKeydown"
        ></textarea>
        <button
          class="btn btn-primary btn-send"
          :disabled="isStreaming || !inputText.trim()"
          @click="send"
        >
          Send
        </button>
      </div>
    </div>
  </div>
</template>
