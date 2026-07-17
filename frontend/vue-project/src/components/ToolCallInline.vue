<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ApprovalStatus, ToolCallInfo } from '@/domain/models'
import { presentToolEvidence } from '@/presentation/tool-evidence'

const props = defineProps<{
  tool_call: ToolCallInfo
}>()

const status_label = computed(() => ({
  RUNNING: 'Running…',
  SUCCEEDED: 'Done',
  FAILED: 'Failed',
  REJECTED: 'Rejected',
  PENDING_APPROVAL: 'Pending',
}[props.tool_call.execution_status] ?? props.tool_call.execution_status))

const status_class = computed(() => ({
  RUNNING: 'text-warning',
  SUCCEEDED: 'text-success',
  FAILED: 'text-danger',
  REJECTED: 'text-danger',
  PENDING_APPROVAL: 'text-muted',
}[props.tool_call.execution_status] ?? 'text-muted'))

const approval_labels: Record<ApprovalStatus, string> = {
  PENDING: 'Pending approval',
  APPROVED: 'Approved',
  REJECTED: 'Approval rejected',
  EXPIRED: 'Approval expired',
}
const approval_label = computed(() => props.tool_call.approval_status
  ? approval_labels[props.tool_call.approval_status]
  : '')

const elapsed = computed(() => props.tool_call.execution_time_ms !== undefined
  ? `${(props.tool_call.execution_time_ms / 1000).toFixed(1)}s`
  : '')

const rw_label = computed(() => props.tool_call.is_read_only ? 'R' : 'W')
const is_expanded = ref(false)
const copy_status = ref<'Copy result' | 'Copied' | 'Copy failed'>('Copy result')
const evidence = computed(() => presentToolEvidence(props.tool_call))
const details_id = computed(() => `tool-evidence-${props.tool_call.call_id}`)
const toggle_label = computed(() => `${is_expanded.value ? 'Hide' : 'Show'} execution evidence for ${props.tool_call.tool_name}`)

watch(
  [() => props.tool_call.call_id, () => props.tool_call.output],
  () => { copy_status.value = 'Copy result' },
)

async function copyResult(): Promise<void> {
  try {
    await navigator.clipboard.writeText(evidence.value.result?.fullText ?? '')
    copy_status.value = 'Copied'
  } catch {
    copy_status.value = 'Copy failed'
  }
}
</script>

<template>
  <div class="tool-call-inline small text-muted py-1 px-2" data-testid="tool-card">
    <button
      type="button"
      class="tool-evidence-summary"
      data-testid="tool-evidence-summary"
      :aria-expanded="is_expanded"
      :aria-controls="details_id"
      :aria-label="toggle_label"
      @click="is_expanded = !is_expanded"
    >
      <span class="badge" :class="tool_call.is_read_only ? 'bg-secondary' : 'bg-info'" data-testid="tool-read-write">{{ rw_label }}</span>
      <span class="fw-medium" data-testid="tool-name">{{ tool_call.tool_name }}</span>
      <span v-if="approval_label" data-testid="tool-approval-status">{{ approval_label }}</span>
      <span :class="status_class" data-testid="tool-execution-status">{{ status_label }}</span>
      <span v-if="elapsed" class="text-body-tertiary" data-testid="tool-duration">{{ elapsed }}</span>
      <span aria-hidden="true">{{ is_expanded ? '▴' : '▾' }}</span>
    </button>
    <div v-if="is_expanded" :id="details_id" class="tool-evidence-details mt-2" data-testid="tool-evidence-details">
      <dl class="tool-evidence-metadata mb-2">
        <template v-if="tool_call.server">
          <dt>Server</dt>
          <dd data-testid="tool-server">{{ tool_call.server }}</dd>
        </template>
        <template v-if="tool_call.is_rollbackable !== undefined">
          <dt>Rollback</dt>
          <dd data-testid="tool-rollback">{{ tool_call.is_rollbackable ? 'Rollbackable' : 'Not rollbackable' }}</dd>
        </template>
      </dl>
      <section>
        <div class="tool-evidence-label">Parameters</div>
        <pre class="tool-output mb-2" data-testid="tool-parameters">{{ evidence.parameters }}</pre>
      </section>
      <section v-if="tool_call.output !== undefined">
        <div class="d-flex align-items-center justify-content-between">
          <div class="tool-evidence-label">Result</div>
          <button
            type="button"
            class="tool-copy-button"
            data-testid="tool-result-copy"
            aria-label="Copy complete tool result"
            title="Copy complete tool result"
            @click="copyResult"
          >
            {{ copy_status }}
          </button>
        </div>
        <pre class="tool-output mb-0" data-testid="tool-result">{{ evidence.result?.previewText }}</pre>
        <div v-if="evidence.result?.truncated" class="text-body-tertiary mb-2" data-testid="tool-result-truncated">
          Preview truncated at 20 lines or 16 KiB.
        </div>
      </section>
      <section v-if="tool_call.error">
        <div class="tool-evidence-label text-danger">Error</div>
        <pre class="tool-output text-danger mb-0" data-testid="tool-error">{{ evidence.error }}</pre>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tool-evidence-summary {
  align-items: center;
  background: transparent;
  border: 0;
  color: inherit;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0;
  text-align: left;
  width: 100%;
}

.tool-evidence-metadata {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 0.75rem;
}

.tool-evidence-metadata dd {
  margin-bottom: 0;
}

.tool-evidence-label {
  font-weight: 600;
}

.tool-copy-button {
  background: transparent;
  border: 0;
  color: var(--bs-link-color);
  padding: 0.125rem 0;
}

.tool-output {
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  max-width: 100%;
  overflow: auto;
  padding: 0.5rem;
  white-space: pre-wrap;
}
</style>
