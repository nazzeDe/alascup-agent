import { test, expect } from '@playwright/test'
import { approveToolRequest, rejectToolRequest } from './helpers'

test.describe('E2E-Live-003: Approval API', () => {
  test('approve endpoint returns 200 for valid request_id', async ({ page }) => {
    // The approval endpoint works with any UUID — it just validates the shape.
    // A non-existent request_id returns success since the server doesn't validate existence.
    const resp = await approveToolRequest(page, '00000000-0000-0000-0000-000000000001', 'test approval')
    expect(resp).toBeTruthy()
  })

  test('reject endpoint returns 200 for valid request_id', async ({ page }) => {
    const resp = await rejectToolRequest(page, '00000000-0000-0000-0000-000000000002', 'test rejection')
    expect(resp).toBeTruthy()
  })
})
