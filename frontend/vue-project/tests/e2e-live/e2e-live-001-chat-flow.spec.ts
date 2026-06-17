import { test, expect } from '@playwright/test'
import { SEL, sendMessage, waitForAssistantMessage, createSessionAndSend } from './fixtures'

test.describe('E2E-Live-001: Chat Flow', () => {
  test('page loads with required UI elements', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator(SEL.chatInput)).toBeVisible()
    await expect(page.locator(SEL.newSessionButton)).toBeVisible()
  })

  test('create session and send message triggers SSE stream', async ({ page }) => {
    await page.goto('/')
    await createSessionAndSend(page, 'Hello, what can you do?')

    // user message bubble appears immediately
    await expect(page.locator(SEL.userBubble).last()).toBeVisible({ timeout: 5000 })

    // assistant reply appears (real LLM may take time)
    const assistantBubble = await waitForAssistantMessage(page, 45000)
    expect(assistantBubble).not.toBeNull()
  })

  test('send button is disabled while streaming', async ({ page }) => {
    await page.goto('/')
    await sendMessage(page, 'List available tools and explain each one')

    // input should be disabled shortly after sending
    await page.waitForTimeout(500)
    const enabled = await page.locator(SEL.chatInput).isEnabled()
    expect(enabled).toBe(false)
  })
})
