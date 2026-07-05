<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ApprovalEvent } from '@/domain/models'

const props = defineProps<{
  event: ApprovalEvent
}>()

const emit = defineEmits<{
  approve: [requestId: string, message?: string]
  reject: [requestId: string, message?: string]
}>()

const message = ref(props.event.message)

watch(
  () => props.event.request_id,
  () => {
    message.value = props.event.message
  },
)

function approve() {
  emit('approve', props.event.request_id, message.value)
}

function reject() {
  emit('reject', props.event.request_id, message.value)
}
</script>

<template>
  <div
    class="approval-inline my-2 p-2 border rounded bg-warning-subtle"
    data-testid="approval-inline"
    :class="{ 'opacity-50': event.status !== 'pending' }"
  >
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
        data-testid="approval-reason-input"
        placeholder="Message (optional)"
      />
      <button
        class="btn btn-sm btn-success"
        data-testid="approval-approve-button"
        @click="approve"
      >
        Approve
      </button>
      <button
        class="btn btn-sm btn-danger"
        data-testid="approval-reject-button"
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
