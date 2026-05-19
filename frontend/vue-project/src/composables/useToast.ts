import { ref, type Ref } from 'vue'

export type ToastType = 'error' | 'warning' | 'info' | 'success'

export interface Toast {
  id: string
  type: ToastType
  message: string
  code?: string
  duration: number
}

const toasts: Ref<Toast[]> = ref([])
let nextId = 0

export function useToast() {
  function showToast(type: ToastType, message: string, code?: string, duration = 8000): void {
    const id = `toast-${nextId++}`
    const toast: Toast = { id, type, message, code, duration }
    toasts.value = [...toasts.value, toast]
    setTimeout(() => dismissToast(id), duration)
  }

  function dismissToast(id: string): void {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, showToast, dismissToast }
}
