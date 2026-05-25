<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  visible: boolean
  tool_name: string
  params: Record<string, unknown>
  reason: string
  request_id: string
  is_processing?: boolean
}>()

const emit = defineEmits<{
  approve: [requestId: string]
  reject: [requestId: string, reason?: string]
}>()

const rejectReason = ref('')
</script>

<template>
  <div v-if="visible" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,0.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header bg-warning-subtle">
          <h5 class="modal-title">Approve Tool Execution</h5>
        </div>
        <div class="modal-body">
          <p><strong>Tool:</strong> <code>{{ tool_name }}</code></p>
          <p><strong>Reason:</strong> {{ reason }}</p>
          <div v-if="params && Object.keys(params).length">
            <strong>Parameters:</strong>
            <pre class="small bg-light p-2 rounded mt-1">{{ JSON.stringify(params, null, 2) }}</pre>
          </div>
          <div class="mt-3">
            <label class="form-label">Rejection reason (optional):</label>
            <textarea v-model="rejectReason" class="form-control" rows="2" placeholder="Why are you rejecting?"></textarea>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-danger btn-reject" :disabled="is_processing" @click="emit('reject', request_id, rejectReason || undefined)">
            <span v-if="is_processing" class="spinner-border spinner-border-sm me-1"></span>
            Reject
          </button>
          <button class="btn btn-success btn-approve" :disabled="is_processing" @click="emit('approve', request_id)">
            <span v-if="is_processing" class="spinner-border spinner-border-sm me-1"></span>
            Approve
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
