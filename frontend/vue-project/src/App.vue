<script setup lang="ts">
import { ref } from 'vue'
import { onMounted } from 'vue'
import { useChat } from '@/composables/useChat'
import { useSessions } from '@/composables/useSessions'
import SessionList from '@/components/SessionList.vue'
import ChatView from '@/components/ChatView.vue'
import ApprovalModal from '@/components/ApprovalModal.vue'
import ToastContainer from '@/components/ToastContainer.vue'

const {
  messages, toolCalls, reasonings, is_streaming, phaseLabel,
  approval_pending, isLoadingHistory,
  sendMessage, submitApproval, loadHistory, resetChat, abort,
} = useChat({
  on_chat_created(chatId: string) {
    activeChatId.value = chatId
    loadSessions()
  },
})
const {
  sessions, activeChatId, isLoadingSessions, isCreatingSession, loadError,
  loadSessions, createSession, switchSession,
} = useSessions()

const isProcessingApproval = ref(false)

onMounted(() => {
  loadSessions()
})

async function handleSelectSession(chatId: string) {
  const ok = await loadHistory(chatId)
  if (ok) switchSession(chatId)
}

async function handleCreateSession() {
  const chatId = await createSession()
  if (chatId) resetChat()
}

function handleSendMessage(text: string) {
  sendMessage(text, activeChatId.value)
}

function handleAbort() {
  abort()
}

async function handleApprove(requestId: string) {
  isProcessingApproval.value = true
  await submitApproval(requestId, 'APPROVED')
  isProcessingApproval.value = false
}

async function handleReject(requestId: string, reason?: string) {
  isProcessingApproval.value = true
  await submitApproval(requestId, 'REJECTED', reason)
  isProcessingApproval.value = false
}
</script>

<template>
  <div class="app-container vh-100 d-flex flex-column">
    <nav class="navbar navbar-dark bg-dark px-3">
      <span class="navbar-brand mb-0 h1">Alascup Agent</span>
      <span class="text-light small">AI Ops Platform</span>
    </nav>

    <div class="row flex-grow-1 m-0 overflow-hidden flex-nowrap">
      <div class="col-md-3 col-lg-2 p-0 border-end bg-light d-flex flex-column overflow-hidden">
        <SessionList
          :sessions="sessions"
          :active_chat_id="activeChatId"
          :is_creating="isCreatingSession"
          :is_loading="isLoadingSessions"
          :error="loadError"
          @select="handleSelectSession"
          @create="handleCreateSession"
        />
      </div>

      <div class="col-md-9 col-lg-10 p-0 d-flex flex-column overflow-hidden">
        <ChatView
          :messages="messages"
          :tool_calls="toolCalls"
          :reasonings="reasonings"
          :is_streaming="is_streaming"
          :phase_label="phaseLabel"
          :is_loading_history="isLoadingHistory"
          @send_message="handleSendMessage"
          @abort="handleAbort"
        />
      </div>
    </div>

    <ApprovalModal
      v-if="approval_pending"
      :visible="true"
      :tool_name="approval_pending.tool_name"
      :params="approval_pending.params"
      :reason="approval_pending.reason"
      :request_id="approval_pending.request_id"
      :is_processing="isProcessingApproval"
      @approve="handleApprove"
      @reject="handleReject"
    />

    <ToastContainer />
  </div>
</template>
