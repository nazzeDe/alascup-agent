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
    case 'RUNNING': return 'Executing…'
    case 'SUCCEEDED': return 'Completed'
    case 'FAILED': return 'Failed'
    default: return props.toolCall.execution_status
  }
})

const timeLabel = computed(() => {
  const ms = props.toolCall.execution_time_ms
  if (ms === undefined || ms === null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
})

const paramEntries = computed(() => {
  const p = props.toolCall.params
  if (!p || Object.keys(p).length === 0) return []
  return Object.entries(p)
})

const hasOutput = computed(() => {
  return props.toolCall.output && Object.keys(props.toolCall.output).length > 0
})

const toolLabel = computed(() => {
  if (props.toolCall.server) return `${props.toolCall.server} / ${props.toolCall.tool_name}`
  return props.toolCall.tool_name
})
</script>

<template>
  <div class="tool-call-card border rounded-3 p-2 my-2 bg-white" style="max-width: 440px">
    <div class="d-flex justify-content-between align-items-center">
      <div>
        <span v-if="toolCall.is_read_only" class="badge bg-info me-1">R</span>
        <span v-else class="badge bg-warning me-1">W</span>
        <strong class="tool-name">{{ toolLabel }}</strong>
      </div>
      <div class="d-flex align-items-center gap-2">
        <span v-if="timeLabel" class="small text-muted">{{ timeLabel }}</span>
        <span :class="statusClass" class="small">{{ statusLabel }}</span>
      </div>
    </div>

    <div v-if="paramEntries.length > 0" class="small mt-1">
      <div v-for="[key, value] in paramEntries" :key="key" class="d-flex">
        <span class="text-muted me-1" style="min-width: 80px">{{ key }}:</span>
        <span class="text-truncate">{{ typeof value === 'string' ? value : JSON.stringify(value) }}</span>
      </div>
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
