<script setup lang="ts">
import type { ToolCallInfo } from '@/types'

const props = defineProps<{
  tool_call: ToolCallInfo
}>()

const status_label = {
  RUNNING: 'Running…',
  SUCCEEDED: 'Done',
  FAILED: 'Failed',
  PENDING_APPROVAL: 'Pending',
}[props.tool_call.execution_status] ?? props.tool_call.execution_status

const status_class = {
  RUNNING: 'text-warning',
  SUCCEEDED: 'text-success',
  FAILED: 'text-danger',
  PENDING_APPROVAL: 'text-muted',
}[props.tool_call.execution_status] ?? 'text-muted'

const elapsed = props.tool_call.execution_time_ms
  ? `${(props.tool_call.execution_time_ms / 1000).toFixed(1)}s`
  : ''

const rw_label = props.tool_call.is_read_only ? 'R' : 'W'
</script>

<template>
  <div class="tool-call-inline small text-muted py-1 px-2">
    <span class="badge me-1" :class="tool_call.is_read_only ? 'bg-secondary' : 'bg-info'">{{ rw_label }}</span>
    <span class="fw-medium">{{ tool_call.tool_name }}</span>
    <span v-if="tool_call.params && Object.keys(tool_call.params).length" class="ms-1 text-body-tertiary">
      ({{ Object.entries(tool_call.params).slice(0, 2).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')
      }}<span v-if="Object.keys(tool_call.params).length > 2">…</span>)
    </span>
    <span class="ms-2" :class="status_class">— {{ status_label }}</span>
    <span v-if="elapsed" class="ms-1 text-body-tertiary">{{ elapsed }}</span>
    <div v-if="tool_call.execution_status === 'FAILED' && tool_call.error" class="text-danger small mt-1">
      {{ tool_call.error.message || 'Unknown error' }}
    </div>
  </div>
</template>
