<script setup lang="ts">
import { useToast } from '@/composables/useToast'

const { toasts, dismissToast } = useToast()
</script>

<template>
  <div class="toast-container position-fixed top-0 end-0 p-3" style="z-index: 1060">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="toast show"
      role="alert"
    >
      <div :class="['toast-header', {
        'bg-danger text-white': toast.type === 'error',
        'bg-warning': toast.type === 'warning',
        'bg-info text-white': toast.type === 'info',
        'bg-success text-white': toast.type === 'success',
      }]">
        <strong class="me-auto">{{ toast.type.toUpperCase() }}</strong>
        <small v-if="toast.code" class="me-2">{{ toast.code }}</small>
        <button type="button" class="btn-close" :class="{ 'btn-close-white': toast.type !== 'warning' }" @click="dismissToast(toast.id)"></button>
      </div>
      <div class="toast-body">
        {{ toast.message }}
      </div>
    </div>
  </div>
</template>
