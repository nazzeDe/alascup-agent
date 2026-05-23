<script setup lang="ts">
import { ref } from 'vue'
import { onMounted } from 'vue'
import { useChat } from '@/composables/useChat'
import { useSessions } from '@/composables/useSessions'
import SessionList from '@/components/SessionList.vue'
import ChatView from '@/components/ChatView.vue'
import ApprovalModal from '@/components/ApprovalModal.vue'
import ToastContainer from '@/components/ToastContainer.vue'

const { messages, toolCalls, reasonings, isStreaming, phaseLabel, approvalPending, isLoadingHistory, sendMessage, submitApproval, loadHistory, abort } = useChat()
const { sessions, activeChatId, isLoadingSessions, isCreatingSession, loadSessions, createSession, switchSession } = useSessions()

const isProcessingApproval = ref(false)

onMounted(() => {
  loadSessions()
})

async function handleSelectSession(chatId: string) {
  await switchSession(chatId)
  await loadHistory(chatId)
}

async function handleCreateSession() {
  await createSession()
  messages.value = []
  toolCalls.value = new Map()
  reasonings.value = []
}

function handleSendMessage(text: string) {
  sendMessage(text)
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
          :active-chat-id="activeChatId"
          :is-creating="isCreatingSession"
          :is-loading="isLoadingSessions"
          @select="handleSelectSession"
          @create="handleCreateSession"
        />
      </div>

      <div class="col-md-9 col-lg-10 p-0 d-flex flex-column overflow-hidden">
        <ChatView
          :messages="messages"
          :tool-calls="toolCalls"
          :reasonings="reasonings"
          :is-streaming="isStreaming"
          :phase-label="phaseLabel"
          :is-loading-history="isLoadingHistory"
          @send-message="handleSendMessage"
          @abort="handleAbort"
        />
      </div>
    </div>

    <ApprovalModal
      v-if="approvalPending"
      :visible="true"
      :tool-name="approvalPending.tool_name"
      :params="approvalPending.params"
      :reason="approvalPending.reason"
      :request-id="approvalPending.request_id"
      :is-processing="isProcessingApproval"
      @approve="handleApprove"
      @reject="handleReject"
    />

    <ToastContainer />
  </div>
</template>
