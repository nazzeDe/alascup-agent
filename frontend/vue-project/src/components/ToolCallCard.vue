<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolCallInfo } from '@/types'

const props = defineProps<{ toolCall: ToolCallInfo }>()
const outputExpanded = ref(false)

const statusClass = computed(() => {
  switch (props.toolCall.execution_status) {
    case 'SUCCEEDED': return 'text-success'
    case 'FAILED': return 'text-danger'
    case 'RUNNING': return 'text-warning'
    case 'PENDING_APPROVAL': return 'text-info'
    default: return ''
  }
})

const statusLabel = computed(() => {
  switch (props.toolCall.execution_status) {
    case 'PENDING_APPROVAL': return 'Waiting Approval'
    case 'RUNNING': return 'Executing...'
    case 'SUCCEEDED': return 'Completed'
    case 'FAILED': return 'Failed'
    default: return props.toolCall.execution_status
  }
})

const hasOutput = computed(() => {
  return props.toolCall.output && Object.keys(props.toolCall.output).length > 0
})
</script>

<template>
  <div class="tool-call-card border rounded-3 p-2 my-2 bg-white" style="max-width: 400px">
    <div class="d-flex justify-content-between align-items-center">
      <div>
        <span v-if="toolCall.is_read_only" class="badge bg-info me-1">R</span>
        <span v-else class="badge bg-warning me-1">W</span>
        <strong class="tool-name">{{ toolCall.tool_name }}</strong>
      </div>
      <div :class="statusClass" class="small">
        {{ statusLabel }}
      </div>
    </div>

    <div v-if="toolCall.params" class="small text-muted mt-1">
      {{ JSON.stringify(toolCall.params) }}
    </div>

    <div v-if="hasOutput" class="mt-2">
      <button class="btn btn-sm btn-outline-secondary tool-output-toggle" @click="outputExpanded = !outputExpanded">
        {{ outputExpanded ? 'Hide' : 'Show' }} Output
      </button>
      <pre v-if="outputExpanded" class="small bg-light p-2 mt-1 rounded">{{ JSON.stringify(toolCall.output, null, 2) }}</pre>
    </div>

    <div v-if="toolCall.error" class="small text-danger mt-1">
      {{ toolCall.error.message }}
    </div>
  </div>
</template>
