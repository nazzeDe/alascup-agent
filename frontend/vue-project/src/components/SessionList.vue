<script setup lang="ts">
import type { ChatSession } from '@/types'

defineProps<{
  sessions: ChatSession[]
  activeChatId?: string
  isCreating?: boolean
  isLoading?: boolean
}>()

const emit = defineEmits<{
  select: [chatId: string]
  create: []
}>()
</script>

<template>
  <div class="session-list h-100 d-flex flex-column">
    <div class="p-2 border-bottom">
      <button class="btn btn-primary btn-sm w-100 btn-new-session" :disabled="isCreating" @click="emit('create')">
        <span v-if="isCreating" class="spinner-border spinner-border-sm me-1"></span>
        + New Session
      </button>
    </div>
    <div class="flex-grow-1 overflow-auto">
      <!-- Loading skeleton -->
      <div v-if="isLoading" class="p-3 text-muted small text-center">
        <div class="spinner-border spinner-border-sm" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        Loading sessions...
      </div>
      <div
        v-for="session in sessions"
        :key="session.chatID"
        :class="['session-item p-2 border-bottom', { 'bg-primary-subtle': session.chatID === activeChatId }]"
        style="cursor: pointer"
        @click="emit('select', session.chatID)"
      >
        <div class="fw-semibold small text-truncate">
          {{ session.title || session.chatID?.slice(0, 8) || 'New Session' }}
        </div>
        <div class="text-muted small">
          {{ new Date(session.timestamp).toLocaleString() }}
        </div>
      </div>
      <div v-if="sessions.length === 0 && !isLoading" class="p-3 text-muted small text-center">
        No sessions yet
      </div>
    </div>
  </div>
</template>
