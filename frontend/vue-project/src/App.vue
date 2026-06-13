<script setup lang="ts">
import { provide, onMounted, shallowRef, type Ref } from 'vue'
import { FetchEventSourceClient } from '@/infrastructure/sse-client'
import { FetchSessionApi } from '@/infrastructure/session-api'
import { FetchApprovalApi } from '@/infrastructure/approval-api'
import { ToastStore } from '@/application/toast-store'
import { ChatStore } from '@/application/chat-store'
import { SessionListStore } from '@/application/session-list-store'
import { SessionService } from '@/application/session-service'
import SessionList from '@/components/SessionList.vue'
import ChatView from '@/components/ChatView.vue'
import ToastContainer from '@/components/ToastContainer.vue'

// Infrastructure
const sseClient = new FetchEventSourceClient()
const sessionApi = new FetchSessionApi()
const approvalApi = new FetchApprovalApi()

// Singleton-like stores
const toastStore = new ToastStore()
const sessionListStore = new SessionListStore()
const sessionService = new SessionService(sessionApi, approvalApi, sessionListStore)

// Active session — swapped on session switch.
// shallowRef prevents Vue from deeply unwrapping ChatStore's inner refs,
// so storeRef.value.messages stays a Ref<Message[]> instead of Message[].
const activeChatStore: Ref<ChatStore> = shallowRef(new ChatStore())

provide('toastStore', toastStore)
provide('sessionListStore', sessionListStore)
provide('sessionService', sessionService)
provide('chatStore', activeChatStore)
provide('sseClient', sseClient)

onMounted(() => {
  sessionService.loadSessions()
})

async function handleSelectSession(chatId: string) {
  sessionListStore.setActive(chatId)
  const newStore = new ChatStore()
  newStore.chatId.value = chatId
  await sessionService.loadHistory(chatId, newStore)
  activeChatStore.value = newStore
}

function handleCreateSession() {
  sessionListStore.setActive(null)
  activeChatStore.value = new ChatStore()
}

async function handleDeleteSession(chatId: string) {
  await sessionService.deleteSession(chatId)
  if (sessionListStore.activeChatId.value === null) {
    activeChatStore.value = new ChatStore()
  }
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
          @select="handleSelectSession"
          @create="handleCreateSession"
          @delete="handleDeleteSession"
        />
      </div>

      <div class="col-md-9 col-lg-10 p-0 d-flex flex-column overflow-hidden">
        <ChatView />
      </div>
    </div>

    <ToastContainer />
  </div>
</template>
