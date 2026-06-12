import { ref, type Ref } from 'vue'

export type ToastType = 'error' | 'warning' | 'info' | 'success'

export interface Toast {
  id: string
  type: ToastType
  message: string
  code?: string
  duration: number
}

export class ToastStore {
  readonly toasts: Ref<Toast[]> = ref([])

  show(type: ToastType, message: string, code?: string, duration = 8000): void {
    const id = crypto.randomUUID()
    const toast: Toast = { id, type, message, code, duration }
    this.toasts.value = [...this.toasts.value, toast]
    setTimeout(() => this.dismiss(id), duration)
  }

  dismiss(id: string): void {
    this.toasts.value = this.toasts.value.filter(t => t.id !== id)
  }
}
