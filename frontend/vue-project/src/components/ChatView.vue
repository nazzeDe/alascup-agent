<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { Message, ToolCallInfo } from '@/types'
import type { ReasoningEntry } from '@/composables/useChat'
import MessageItem from './MessageItem.vue'
import ToolCallCard from './ToolCallCard.vue'
import ReasoningBubble from './ReasoningBubble.vue'

const props = withDefaults(defineProps<{
  messages: Message[]
  tool_calls: Map<string, ToolCallInfo>
  reasonings?: ReasoningEntry[]
  is_streaming: boolean
  is_loading_history?: boolean
  phase_label?: string
}>(), {
  reasonings: () => [],
  phase_label: '',
  is_loading_history: false,
})

const emit = defineEmits<{
  send_message: [text: string]
  abort: []
}>()

const input_text = ref('')
const messages_container = ref<HTMLElement | null>(null)
const user_scrolled_up = ref(false)

type TimelineItem =
  | { type: 'message'; data: Message; ts: number }
  | { type: 'tool_card'; data: ToolCallInfo; ts: number }
  | { type: 'reasoning'; data: ReasoningEntry; ts: number }

const timeline = computed(() => {
  const items: TimelineItem[] = []

  for (const m of props.messages) {
    items.push({ type: 'message', data: m, ts: new Date(m.timestamp).getTime() })
  }
  props.tool_calls.forEach((tc) => {
    items.push({ type: 'tool_card', data: tc, ts: new Date(tc.timestamp).getTime() })
  })
  for (const r of props.reasonings) {
    items.push({ type: 'reasoning', data: r, ts: new Date(r.timestamp).getTime() })
  }

  items.sort((a, b) => a.ts - b.ts)
  return items
})

function check_auto_scroll() {
  if (user_scrolled_up.value) return
  nextTick(() => {
    if (messages_container.value) {
      messages_container.value.scrollTop = messages_container.value.scrollHeight
    }
  })
}

watch(
  () => timeline.value.length,
  check_auto_scroll,
)

function on_scroll() {
  if (!messages_container.value) return
  const { scrollTop, clientHeight, scrollHeight } = messages_container.value
  user_scrolled_up.value = scrollTop + clientHeight < scrollHeight - 50
}

function send() {
  const text = input_text.value.trim()
  if (!text || props.is_streaming) return
  emit('send_message', text)
  input_text.value = ''
}

function on_keydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="chat-view d-flex flex-column flex-grow-1 overflow-hidden">
    <div
      ref="messages_container"
      class="chat-messages flex-grow-1 overflow-auto p-3"
      @scroll="on_scroll"
    >
      <template v-for="item in timeline" :key="item.type + '-' + (item.type === 'message' ? item.data.message_id : item.type === 'reasoning' ? (item.data as ReasoningEntry).message_id : (item.data as ToolCallInfo).message_id)">
        <ReasoningBubble v-if="item.type === 'reasoning'" :reasoning="item.data as ReasoningEntry" />
        <MessageItem v-else-if="item.type === 'message'" :message="item.data as Message" />
        <ToolCallCard v-else :tool_call="item.data as ToolCallInfo" />
      </template>

      <div v-if="is_loading_history" class="chat-loading-overlay text-center p-3">
        <div class="spinner-border text-muted" role="status">
          <span class="visually-hidden">Loading history...</span>
        </div>
      </div>
      <div v-else-if="timeline.length === 0" class="text-center text-muted mt-5">
        <p class="fs-4">Alascup Agent</p>
        <p>Start a conversation — ask about system status, diagnostics, or operations.</p>
      </div>
    </div>

    <!-- Scroll-to-bottom floating button -->
    <div v-if="user_scrolled_up && is_streaming" class="scroll-bottom-btn" @click="user_scrolled_up = false; check_auto_scroll()">
      ↓
    </div>

    <!-- Streaming status bar -->
    <div v-if="is_streaming" class="streaming-status d-flex align-items-center px-3 py-2 border-top">
      <div class="pulse-dot me-2"></div>
      <span class="small text-muted flex-grow-1">{{ phase_label || 'AI is responding…' }}</span>
      <button class="btn btn-outline-danger btn-sm btn-stop" @click="emit('abort')">Stop</button>
    </div>

    <div class="chat-input border-top p-2">
      <div class="input-group">
        <textarea
          v-model="input_text"
          class="form-control"
          rows="2"
          placeholder="Type your message… (Enter to send, Shift+Enter for newline)"
          :disabled="is_streaming"
          @keydown="on_keydown"
        ></textarea>
        <button
          class="btn btn-primary btn-send"
          :disabled="is_streaming || !input_text.trim()"
          @click="send"
        >
          Send
        </button>
      </div>
    </div>
  </div>
</template>
