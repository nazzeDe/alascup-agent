import { test, expect } from '@playwright/test'
import { checkHealth, listTools } from './helpers'

test.describe('E2E-Live-000: Health Smoke', () => {
  test('web-server health endpoint responds ok', async ({ page }) => {
    const health = await checkHealth(page)
    expect(health).toHaveProperty('status', 'ok')
  })

  test('tool list is non-empty after server startup', async ({ page }) => {
    const tools = await listTools(page)
    expect(Array.isArray(tools)).toBe(true)
    expect(tools.length).toBeGreaterThan(0)
  })

  test('frontend loads and shows new session button', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('button:has-text("New Session")').or(page.locator('.btn-new-session'))).toBeVisible({ timeout: 10000 })
  })
})
