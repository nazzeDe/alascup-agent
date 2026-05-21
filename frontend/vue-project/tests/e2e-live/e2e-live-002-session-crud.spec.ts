import { test, expect } from '@playwright/test'
import { SEL, sendMessage, waitForAssistantMessage, waitForStreamToEnd } from './fixtures'
import { createSession, checkHealth } from './helpers'

test.describe('E2E-Live-002: Session CRUD', () => {
  test('backend health check passes', async ({ page, request }) => {
    const health = await checkHealth(page)
    expect(health).toHaveProperty('status', 'ok')
  })

  test('create session via API', async ({ page }) => {
    const id = await createSession(page)
    expect(id).toBeTruthy()
    expect(typeof id).toBe('string')
  })

  test('sessions persist after page reload', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector(SEL.newSessionButton)

    // create session and send a message to ensure it sticks
    await page.click(SEL.newSessionButton)
    await page.waitForTimeout(500)
    await sendMessage(page, 'hello')
    await waitForAssistantMessage(page, 45000)
    await waitForStreamToEnd(page, 45000)

    // reload and verify sidebar has session items
    await page.reload()
    await page.waitForSelector(SEL.newSessionButton)

    const sessionItems = page.locator(SEL.sessionItem)
    const count = await sessionItems.count()
    expect(count).toBeGreaterThan(0)
  })
})
