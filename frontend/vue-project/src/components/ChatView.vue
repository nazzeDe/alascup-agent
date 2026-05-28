<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { Message, ToolCallInfo } from '@/types'
import type { ReasoningEntry, ApprovalEvent, ErrorInfo } from '@/composables/useSessionManager'
import { useSessionManager } from '@/composables/useSessionManager'
import MessageItem from './MessageItem.vue'
import ToolCallInline from './ToolCallInline.vue'
import ReasoningBubble from './ReasoningBubble.vue'
import ApprovalInline from './ApprovalInline.vue'

const props = defineProps<{
  chatId: string | null
}>()

const manager = useSessionManager()

const state = computed(() => manager.get(props.chatId))

const _messages = computed(() => state.value?.messages.value ?? [])
const _toolCalls = computed(() => state.value?.toolCalls.value ?? new Map())
const _reasonings = computed(() => state.value?.reasonings.value ?? [])
const _isStreaming = computed(() => state.value?.isStreaming.value ?? false)
const _phaseLabel = computed(() => state.value?.phaseLabel.value ?? '')
const _approvalEvent = computed(() => state.value?.approvalEvent.value ?? null)
const _draftInput = computed({
  get: () => state.value?.draftInput.value ?? '',
  set: (v: string) => { if (state.value) state.value.draftInput.value = v },
})
const _isLoadingHistory = computed(() => state.value?.isLoadingHistory.value ?? false)
const _connectionError = computed(() => state.value?.connectionError.value ?? null)

const errorBannerClass = computed(() => {
  const code = _connectionError.value?.code
  if (code === 'NETWORK_ERROR') return 'bg-warning-subtle text-warning-emphasis'
  if (code === 'TURN_LIMIT_EXCEEDED' || code === 'TOKEN_BUDGET_EXCEEDED') return 'bg-info-subtle text-info-emphasis'
  return 'bg-danger-subtle text-danger-emphasis'
})

const errorBannerTitle = computed(() => {
  const code = _connectionError.value?.code
  if (code === 'NETWORK_ERROR') return 'Connection lost —'
  if (code === 'TURN_LIMIT_EXCEEDED') return 'Turn limit reached —'
  if (code === 'TOKEN_BUDGET_EXCEEDED') return 'Context too large —'
  if (code === 'AGENT_CRASH' || code === 'SSE_CRASH') return 'Agent error —'
  return 'Error —'
})

const messages_container = ref<HTMLElement | null>(null)
const user_scrolled_up = ref(false)

type TimelineItem =
  | { type: 'message'; data: Message; ts: number }
  | { type: 'tool_call'; data: ToolCallInfo; ts: number }
  | { type: 'reasoning'; data: ReasoningEntry; ts: number }
  | { type: 'approval'; data: ApprovalEvent; ts: number }

const timeline = computed(() => {
  const items: TimelineItem[] = []

  for (const m of _messages.value) {
    items.push({ type: 'message', data: m, ts: new Date(m.timestamp).getTime() })
  }
  _toolCalls.value.forEach((tc) => {
    items.push({ type: 'tool_call', data: tc, ts: new Date(tc.timestamp).getTime() })
  })
  for (const r of _reasonings.value) {
    items.push({ type: 'reasoning', data: r, ts: new Date(r.timestamp).getTime() })
  }

  if (_approvalEvent.value) {
    const ev = _approvalEvent.value
    items.push({ type: 'approval', data: ev, ts: Date.now() })
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
  const s = state.value
  if (!s) return
  const text = _draftInput.value.trim()
  if (!text || _isStreaming.value) return
  s.sendMessage(text)
  _draftInput.value = ''
}

function on_keydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

async function handle_approve(requestId: string, message?: string) {
  await state.value?.approve(requestId, message)
}

async function handle_reject(requestId: string, message?: string) {
  await state.value?.reject(requestId, message)
}
</script>

<template>
  <div class="chat-view d-flex flex-column flex-grow-1 overflow-hidden">
    <div
      ref="messages_container"
      class="chat-messages flex-grow-1 overflow-auto p-3"
      @scroll="on_scroll"
    >
      <template v-if="!state || _isLoadingHistory">
        <div class="chat-loading-overlay text-center p-3">
          <div class="spinner-border text-muted" role="status">
            <span class="visually-hidden">Loading history...</span>
          </div>
        </div>
      </template>
      <template v-else-if="timeline.length === 0 && !_approvalEvent">
        <div class="text-center text-muted mt-5">
          <p class="fs-4">Alascup Agent</p>
          <p>Start a conversation — ask about system status, diagnostics, or operations.</p>
        </div>
      </template>
      <template v-else>
        <template v-for="item in timeline" :key="item.type + '-' + (item.type === 'message' ? item.data.message_id : item.type === 'reasoning' ? (item.data as ReasoningEntry).message_id : item.type === 'tool_call' ? (item.data as ToolCallInfo).message_id : (item.data as ApprovalEvent).request_id)">
          <ReasoningBubble v-if="item.type === 'reasoning'" :reasoning="item.data as ReasoningEntry" />
          <MessageItem v-else-if="item.type === 'message'" :message="item.data as Message" />
          <ToolCallInline v-else-if="item.type === 'tool_call'" :tool_call="item.data as ToolCallInfo" />
          <ApprovalInline
            v-else-if="item.type === 'approval'"
            :event="item.data as ApprovalEvent"
            @approve="handle_approve"
            @reject="handle_reject"
          />
        </template>
      </template>
    </div>

    <!-- Scroll-to-bottom floating button -->
    <div v-if="user_scrolled_up && _isStreaming" class="scroll-bottom-btn" @click="user_scrolled_up = false; check_auto_scroll()">
      ↓
    </div>

    <!-- Streaming status bar -->
    <div v-if="_isStreaming" class="streaming-status d-flex align-items-center px-3 py-2 border-top">
      <div class="pulse-dot me-2"></div>
      <span class="small text-muted flex-grow-1">{{ _phaseLabel || 'AI is responding…' }}</span>
      <button class="btn btn-outline-danger btn-sm btn-stop" @click="state?.abort()">Stop</button>
    </div>

    <!-- Connection error banner -->
    <div v-if="_connectionError" class="connection-error px-3 py-2 border-top small" :class="errorBannerClass">
      <span class="fw-semibold">{{ errorBannerTitle }}</span>
      <span class="ms-1">{{ _connectionError.message }}</span>
    </div>

    <div class="chat-input border-top p-2">
      <div class="input-group">
        <textarea
          v-if="state"
          v-model="_draftInput"
          class="form-control"
          rows="2"
          placeholder="Type your message… (Enter to send, Shift+Enter for newline)"
          :disabled="_isStreaming"
          @keydown="on_keydown"
        ></textarea>
        <button
          v-if="state"
          class="btn btn-primary btn-send"
          :disabled="_isStreaming || !_draftInput.trim()"
          @click="send"
        >
          Send
        </button>
      </div>
    </div>
  </div>
</template>
