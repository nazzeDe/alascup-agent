import type { ApprovalApi } from '@/application/ports'

export class FetchApprovalApi implements ApprovalApi {
  async approve(requestId: string, reason?: string): Promise<void> {
    const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_status: 'APPROVED', reason: reason || '' }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  }

  async reject(requestId: string, reason?: string): Promise<void> {
    const res = await fetch(`/api/tool-requests/${requestId}/approval`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_status: 'REJECTED', reason: reason || '' }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  }
}
