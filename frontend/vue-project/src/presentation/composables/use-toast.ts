import { inject } from 'vue'
import { ToastStore } from '@/application/toast-store'

export function useToast() {
  const store = inject<ToastStore>('toastStore')!
  return {
    toasts: store.toasts,
    dismissToast: (id: string) => store.dismiss(id),
  }
}
