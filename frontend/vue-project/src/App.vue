<script setup lang="ts">
import { onMounted } from 'vue'
import { useSessionManager } from '@/composables/useSessionManager'
import SessionList from '@/components/SessionList.vue'
import ChatView from '@/components/ChatView.vue'
import ToastContainer from '@/components/ToastContainer.vue'

const manager = useSessionManager()

onMounted(() => {
  manager.loadSessions()
})

function handleSelectSession(chatId: string) {
  manager.loadHistory(chatId)
}

function handleCreateSession() {
  manager.createDraft()
}

function handleDeleteSession(chatId: string) {
  manager.deleteSession(chatId)
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
          :sessions="manager.sessions.value"
          :active_chat_id="manager.activeChatId.value"
          :is_creating="false"
          :is_loading="manager.isLoadingSessions.value"
          :error="manager.loadError.value"
          @select="handleSelectSession"
          @create="handleCreateSession"
          @delete="handleDeleteSession"
        />
      </div>

      <div class="col-md-9 col-lg-10 p-0 d-flex flex-column overflow-hidden">
        <ChatView :chat-id="manager.activeChatId.value" />
      </div>
    </div>

    <ToastContainer />
  </div>
</template>
