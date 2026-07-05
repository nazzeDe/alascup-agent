<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import type { ChatSession } from '@/domain/models'
import { useSessionList } from '@/presentation/composables/use-session-list'

const emit = defineEmits<{
  select: [chatId: string]
  create: []
  delete: [chatId: string]
}>()

const ctxMenu = ref<{ visible: boolean; x: number; y: number; chatId: string | null }>({
  visible: false, x: 0, y: 0, chatId: null,
})

function onContextMenu(e: MouseEvent, chatId: string) {
  e.preventDefault()
  ctxMenu.value = { visible: true, x: e.clientX, y: e.clientY, chatId }
}

function closeMenu() {
  ctxMenu.value.visible = false
}

function handleDelete() {
  const chatId = ctxMenu.value.chatId
  if (chatId) emit('delete', chatId)
  closeMenu()
}

function onDocClick() {
  closeMenu()
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

const { sessions, activeChatId, isLoading, error } = useSessionList()

interface Group {
  label: string
  sessions: ChatSession[]
}

const groups = computed<Group[]>(() => {
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterdayStart = new Date(todayStart.getTime() - 86400000)
  const weekStart = new Date(todayStart.getTime() - 7 * 86400000)
  const monthStart = new Date(todayStart.getTime() - 30 * 86400000)

  const result: Group[] = [
    { label: 'Today', sessions: [] },
    { label: 'Yesterday', sessions: [] },
    { label: 'Previous 7 days', sessions: [] },
    { label: 'Previous 30 days', sessions: [] },
    { label: 'Older', sessions: [] },
  ]

  for (const s of sessions.value) {
    const d = new Date(s.timestamp)
    if (d >= todayStart) {
      result[0]!.sessions.push(s)
    } else if (d >= yesterdayStart) {
      result[1]!.sessions.push(s)
    } else if (d >= weekStart) {
      result[2]!.sessions.push(s)
    } else if (d >= monthStart) {
      result[3]!.sessions.push(s)
    } else {
      result[4]!.sessions.push(s)
    }
  }

  return result.filter(g => g.sessions.length > 0)
})
</script>

<template>
  <div class="session-list flex-grow-1 d-flex flex-column">
    <div class="p-2 border-bottom">
      <button
        class="btn btn-primary btn-sm w-100 btn-new-session"
        data-testid="new-session-button"
        @click="emit('create')"
      >
        + New Session
      </button>
    </div>
    <div class="flex-grow-1 overflow-auto">
      <div v-if="isLoading" class="p-3 text-muted small text-center">
        <div class="spinner-border spinner-border-sm" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        Loading sessions...
      </div>
      <template v-else>
        <template v-for="group in groups" :key="group.label">
          <div class="session-group-header">{{ group.label }}</div>
          <div
            v-for="session in group.sessions"
            :key="session.chat_id"
            :class="['session-item p-2 border-bottom', { active: session.chat_id === activeChatId }]"
            data-testid="session-item"
            @click="emit('select', session.chat_id)"
            @contextmenu="onContextMenu($event, session.chat_id)"
          >
            <div class="fw-semibold small text-truncate">
              {{ session.title || session.chat_id?.slice(0, 8) || 'New Session' }}
            </div>
            <div class="text-muted small">
              {{ new Date(session.timestamp).toLocaleString() }}
            </div>
          </div>
        </template>
        <div v-if="error" class="p-3 text-danger small text-center">
          {{ error }}
        </div>
        <div v-else-if="groups.length === 0" class="p-3 text-muted small text-center">
          No sessions yet
        </div>
      </template>
    </div>

    <div
      v-if="ctxMenu.visible"
      class="ctx-menu"
      :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
      @click.stop
    >
      <div class="ctx-menu-item text-danger" @click="handleDelete">删除</div>
    </div>
  </div>
</template>

<style scoped>
.ctx-menu {
  position: fixed;
  z-index: 9999;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  padding: 4px 0;
  min-width: 100px;
}
.ctx-menu-item {
  padding: 6px 12px;
  cursor: pointer;
  font-size: 0.875rem;
}
.ctx-menu-item:hover {
  background: #f5f5f5;
}
</style>
