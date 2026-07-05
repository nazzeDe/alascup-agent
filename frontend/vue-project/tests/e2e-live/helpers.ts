import type { Page } from '@playwright/test'

const API_BASE = (
  process.env.E2E_API_BASE ??
  process.env.E2E_BASE_URL ??
  'http://localhost:11450'
).replace(/\/$/, '')

export async function approveToolRequest(
  page: Page,
  requestId: string,
  reason?: string,
): Promise<Record<string, unknown>> {
  const resp = await page.request.post(
    `${API_BASE}/api/tool-requests/${requestId}/approval`,
    {
      data: { approval_status: 'approved', reason: reason ?? '' },
    },
  )
  return resp.json()
}

export async function rejectToolRequest(
  page: Page,
  requestId: string,
  reason?: string,
): Promise<Record<string, unknown>> {
  const resp = await page.request.post(
    `${API_BASE}/api/tool-requests/${requestId}/approval`,
    {
      data: { approval_status: 'rejected', reason: reason ?? '' },
    },
  )
  return resp.json()
}

export async function listTools(page: Page): Promise<unknown[]> {
  const resp = await page.request.get(`${API_BASE}/api/tools`)
  if (!resp.ok()) throw new Error(`list tools failed: HTTP ${resp.status()}`)
  return resp.json()
}

export async function listSessions(page: Page): Promise<unknown[]> {
  const resp = await page.request.get(`${API_BASE}/api/sessions`)
  if (!resp.ok()) throw new Error(`list sessions failed: HTTP ${resp.status()}`)
  return resp.json()
}

export async function checkHealth(page: Page): Promise<Record<string, unknown>> {
  const resp = await page.request.get(`${API_BASE}/api/health`)
  if (!resp.ok()) throw new Error(`health check failed: HTTP ${resp.status()}`)
  return resp.json()
}
