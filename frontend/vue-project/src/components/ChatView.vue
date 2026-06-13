<script setup lang="ts">
import { ref, computed, inject, type Ref } from 'vue'
import type { Message, ToolCallInfo, ReasoningEntry, ApprovalEvent, ErrorInfo } from '@/domain/models'
import { useChat } from '@/presentation/composables/use-chat'
import { useTimeline } from '@/presentation/composables/use-timeline'
import { useAutoScroll } from '@/presentation/composables/use-auto-scroll'
import type { SessionService } from '@/application/session-service'
import type { ChatStore } from '@/application/chat-store'
import MessageItem from './MessageItem.vue'
import ToolCallInline from './ToolCallInline.vue'
import ReasoningBubble from './ReasoningBubble.vue'
import ApprovalInline from './ApprovalInline.vue'

const {
  messages,
  toolCalls,
  reasonings,
  isStreaming,
  draftInput,
  approvalEvent,
  phaseLabel,
  isLoadingHistory,
  connectionError,
  send,
  abort,
} = useChat()

const timeline = useTimeline(messages, toolCalls, reasonings, approvalEvent)

const messages_container = ref<HTMLElement | null>(null)
const { isScrolledUp: user_scrolled_up, handleScroll: on_scroll, scrollToBottom: check_auto_scroll } =
  useAutoScroll(messages_container, computed(() => timeline.value.length))

const errorBannerClass = computed(() => {
  const code = connectionError.value?.code
  if (code === 'NETWORK_ERROR') return 'bg-warning-subtle text-warning-emphasis'
  if (code === 'TURN_LIMIT_EXCEEDED' || code === 'TOKEN_BUDGET_EXCEEDED') return 'bg-info-subtle text-info-emphasis'
  return 'bg-danger-subtle text-danger-emphasis'
})

const errorBannerTitle = computed(() => {
  const code = connectionError.value?.code
  if (code === 'NETWORK_ERROR') return 'Connection lost —'
  if (code === 'TURN_LIMIT_EXCEEDED') return 'Turn limit reached —'
  if (code === 'TOKEN_BUDGET_EXCEEDED') return 'Context too large —'
  if (code === 'AGENT_CRASH' || code === 'SSE_CRASH') return 'Agent error —'
  return 'Error —'
})

const sessionService = inject<SessionService>('sessionService')!
const chatStoreRef = inject<Ref<ChatStore>>('chatStore')!

function sendMessage() {
  const text = draftInput.value.trim()
  if (!text || isStreaming.value) return
  send(text)
  draftInput.value = ''
}

function on_keydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

async function handle_approve(requestId: string, message?: string) {
  await sessionService.approve(requestId, chatStoreRef.value, message)
}

async function handle_reject(requestId: string, message?: string) {
  await sessionService.reject(requestId, chatStoreRef.value, message)
}
</script>

<template>
  <div class="chat-view d-flex flex-column flex-grow-1 overflow-hidden">
    <div
      ref="messages_container"
      class="chat-messages flex-grow-1 overflow-auto p-3"
      @scroll="on_scroll"
    >
      <template v-if="isLoadingHistory">
        <div class="chat-loading-overlay text-center p-3">
          <div class="spinner-border text-muted" role="status">
            <span class="visually-hidden">Loading history...</span>
          </div>
        </div>
      </template>
      <template v-else-if="timeline.length === 0 && !approvalEvent">
        <div class="text-center text-muted mt-5">
          <p class="fs-4">Alascup Agent</p>
          <p>Start a conversation — ask about system status, diagnostics, or operations.</p>
        </div>
      </template>
      <template v-else>
        <template v-for="item in timeline" :key="item.type + '-' + (item.type === 'message' ? item.data.message_id : item.type === 'reasoning' ? (item.data as ReasoningEntry).message_id : item.type === 'tool_call' ? (item.data as ToolCallInfo).call_id : (item.data as ApprovalEvent).request_id)">
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
        <div v-if="isStreaming && phaseLabel" class="d-flex justify-content-end pe-3 mb-1">
          <small class="phase-label-text">
            <span class="pulse-dot d-inline-block me-1" style="width:6px;height:6px;vertical-align:middle"></span>{{ phaseLabel }}
          </small>
        </div>
      </template>
    </div>

    <div v-if="user_scrolled_up && isStreaming" class="scroll-bottom-btn" @click="user_scrolled_up = false; check_auto_scroll()">
      ↓
    </div>

    <div v-if="connectionError" class="connection-error px-3 py-2 border-top small" :class="errorBannerClass">
      <span class="fw-semibold">{{ errorBannerTitle }}</span>
      <span class="ms-1">{{ connectionError.message }}</span>
    </div>

    <div class="chat-input border-top p-2">
      <div class="input-group">
        <textarea
          v-model="draftInput"
          class="form-control"
          rows="2"
          placeholder="Type your message… (Enter to send, Shift+Enter for newline)"
          :disabled="isStreaming"
          @keydown="on_keydown"
        ></textarea>
        <button
          class="btn-send-btn"
          :class="{ 'btn-stop-btn': isStreaming }"
          :disabled="!isStreaming && !draftInput.trim()"
          @click="isStreaming ? abort() : sendMessage()"
          :title="isStreaming ? 'Stop generating' : 'Send message'"
        >
          <svg v-if="isStreaming" width="16" height="16" viewBox="0 0 16 16" fill="none">
            <rect x="3" y="3" width="10" height="10" rx="2" fill="currentColor"/>
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8L14 2L8 14L7 9L2 8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── Phase status label above AI bubble ── */
.phase-label-text {
  color: #6b7280;
  font-size: 0.8rem;
}

/* ── Round send/stop button ── */
.btn-send-btn {
  width: 38px;
  height: 38px;
  border-radius: 50% !important;
  border: none;
  background: #0d6efd;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background 0.15s ease, transform 0.15s ease;
}
.btn-send-btn:hover:not(:disabled) {
  background: #0b5ed7;
  transform: scale(1.05);
}
.btn-send-btn:disabled {
  background: #dee2e6;
  color: #adb5bd;
  cursor: not-allowed;
}
.btn-stop-btn {
  background: #dc3545;
}
.btn-stop-btn:hover:not(:disabled) {
  background: #bb2d3b;
}
</style>
