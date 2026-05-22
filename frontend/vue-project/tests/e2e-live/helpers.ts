import type { Page } from '@playwright/test'

const API_BASE = 'http://localhost:11450'

export async function createSession(page: Page): Promise<string> {
  const resp = await page.request.post(`${API_BASE}/api/sessions`)
  const json = await resp.json()
  return json.id
}

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
  return resp.json()
}

export async function checkHealth(page: Page): Promise<Record<string, unknown>> {
  const resp = await page.request.get(`${API_BASE}/api/health`)
  return resp.json()
}
