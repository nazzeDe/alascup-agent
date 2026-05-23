<script setup lang="ts">
import { ref, computed, watch, nextTick, withDefaults } from 'vue'
import type { Message, ToolCallInfo } from '@/types'
import type { ReasoningEntry } from '@/composables/useChat'
import MessageItem from './MessageItem.vue'
import ToolCallCard from './ToolCallCard.vue'
import ReasoningBubble from './ReasoningBubble.vue'

const props = withDefaults(defineProps<{
  messages: Message[]
  toolCalls: Map<string, ToolCallInfo>
  reasonings?: ReasoningEntry[]
  isStreaming: boolean
  isLoadingHistory?: boolean
  phaseLabel?: string
}>(), {
  reasonings: () => [],
  phaseLabel: '',
  isLoadingHistory: false,
})

const emit = defineEmits<{
  'send-message': [text: string]
  abort: []
}>()

const inputText = ref('')
const messagesContainer = ref<HTMLElement | null>(null)
const userScrolledUp = ref(false)

type TimelineItem =
  | { type: 'message'; data: Message; ts: number }
  | { type: 'tool_card'; data: ToolCallInfo; ts: number }
  | { type: 'reasoning'; data: ReasoningEntry; ts: number }

const timeline = computed(() => {
  const items: TimelineItem[] = []

  for (const m of props.messages) {
    items.push({ type: 'message', data: m, ts: new Date(m.timestamp).getTime() })
  }
  props.toolCalls.forEach((tc) => {
    items.push({ type: 'tool_card', data: tc, ts: new Date(tc.timestamp).getTime() })
  })
  for (const r of props.reasonings) {
    items.push({ type: 'reasoning', data: r, ts: new Date(r.timestamp).getTime() })
  }

  items.sort((a, b) => a.ts - b.ts)
  return items
})

function checkAutoScroll() {
  if (userScrolledUp.value) return
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(
  () => props.messages.map(m => m.content).join('') + '|' + props.messages.length + '|' + props.toolCalls.size + '|' + (props.reasonings?.length ?? 0),
  checkAutoScroll,
)

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
      <template v-for="item in timeline" :key="item.type + '-' + (item.type === 'message' ? item.data.message_id : item.type === 'reasoning' ? (item.data as ReasoningEntry).message_id : (item.data as ToolCallInfo).message_id)">
        <ReasoningBubble v-if="item.type === 'reasoning'" :reasoning="item.data as ReasoningEntry" />
        <MessageItem v-else-if="item.type === 'message'" :message="item.data as Message" />
        <ToolCallCard v-else :tool-call="item.data as ToolCallInfo" />
      </template>

      <div v-if="timeline.length === 0 && !isLoadingHistory" class="text-center text-muted mt-5">
        <p class="fs-4">Alascup Agent</p>
        <p>Start a conversation — ask about system status, diagnostics, or operations.</p>
      </div>
    </div>

    <!-- Streaming status bar with phase -->
    <div v-if="isStreaming" class="streaming-status d-flex align-items-center px-3 py-1 border-top bg-light">
      <div class="spinner-border spinner-border-sm text-primary me-2" role="status">
        <span class="visually-hidden">Loading...</span>
      </div>
      <span class="small text-muted flex-grow-1">{{ phaseLabel || 'AI is responding…' }}</span>
      <button class="btn btn-outline-danger btn-sm btn-stop" @click="emit('abort')">Stop</button>
    </div>

    <div class="chat-input border-top p-2">
      <div class="input-group">
        <textarea
          v-model="inputText"
          class="form-control"
          rows="2"
          placeholder="Type your message… (Enter to send, Shift+Enter for newline)"
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
