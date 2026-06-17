import { test, expect } from '@playwright/test'
import { SEL, sendMessage } from './fixtures'
import { listSessions } from './helpers'

test.describe('E2E-Live-002: Session CRUD', () => {
  test('list sessions via API', async ({ page }) => {
    const sessions = await listSessions(page)
    expect(Array.isArray(sessions)).toBe(true)
  })

  test('sessions persist after page reload', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector(SEL.newSessionButton)

    // create session and send a message to ensure it sticks
    await page.click(SEL.newSessionButton)
    await page.waitForTimeout(500)
    await sendMessage(page, 'hello')
    await expect(page.locator(SEL.sessionItem).filter({ hasText: 'hello' }).first()).toBeVisible({
      timeout: 10000,
    })

    // reload and verify sidebar has session items
    await page.reload()
    await page.waitForSelector(SEL.newSessionButton)

    const sessionItems = page.locator(SEL.sessionItem)
    const count = await sessionItems.count()
    expect(count).toBeGreaterThan(0)
  })
})
