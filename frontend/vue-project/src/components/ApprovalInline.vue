<script setup lang="ts">
import { ref } from 'vue'
import type { ApprovalEvent } from '@/composables/useSessionManager'

const props = defineProps<{
  event: ApprovalEvent
}>()

const emit = defineEmits<{
  approve: [requestId: string, message?: string]
  reject: [requestId: string, message?: string]
}>()

const message = ref(props.event.message)
const is_processing = ref(false)

async function approve() {
  is_processing.value = true
  emit('approve', props.event.request_id, message.value)
}

async function reject() {
  is_processing.value = true
  emit('reject', props.event.request_id, message.value)
}
</script>

<template>
  <div class="approval-inline my-2 p-2 border rounded bg-warning-subtle" :class="{ 'opacity-50': event.status !== 'pending' }">
    <div class="d-flex align-items-center gap-2 mb-2">
      <span v-if="event.status === 'pending'" class="badge bg-warning text-dark">Awaiting Approval</span>
      <span v-else-if="event.status === 'approved'" class="badge bg-success">Approved</span>
      <span v-else-if="event.status === 'rejected'" class="badge bg-danger">Rejected</span>
      <span class="fw-medium">{{ event.tool_name }}</span>
      <span class="small text-muted">{{ event.reason }}</span>
    </div>

    <div v-if="Object.keys(event.params).length" class="small mb-2">
      <div v-for="(v, k) in event.params" :key="k" class="ms-2">
        <code class="me-1">{{ k }}</code> = <code>{{ JSON.stringify(v) }}</code>
      </div>
    </div>

    <div v-if="event.status === 'pending'" class="d-flex gap-2 align-items-center">
      <input
        v-model="message"
        type="text"
        class="form-control form-control-sm"
        placeholder="Message (optional)"
        :disabled="is_processing"
      />
      <button
        class="btn btn-sm btn-success"
        :disabled="is_processing"
        @click="approve"
      >
        Approve
      </button>
      <button
        class="btn btn-sm btn-danger"
        :disabled="is_processing"
        @click="reject"
      >
        Reject
      </button>
    </div>

    <div v-else-if="event.message" class="small text-muted mt-1">
      "{{ event.message }}"
    </div>
  </div>
</template>
