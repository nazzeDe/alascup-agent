import type { Page, Locator } from '@playwright/test'

/** CSS selectors matching the Vue components. */
export const SEL = {
  chatMessages: '.chat-messages',
  chatInput: '.chat-view textarea',
  sendButton: '.btn-send-btn',
  userBubble: '.chat-bubble-user',
  assistantBubble: '.chat-bubble-assistant',
  systemBubble: '.chat-bubble-system',
  toolCard: '.tool-call-inline',
  toolName: '.tool-call-inline .fw-medium',
  approvalModal: '.approval-inline',
  approveButton: '.approval-inline .btn-success',
  rejectButton: '.approval-inline .btn-danger',
  rejectReasonInput: '.approval-inline input',
  sessionItem: '.session-item',
  newSessionButton: '.btn-new-session',
  emptyChat: '.chat-messages .text-center.text-muted',
  toolOutputToggle: '.tool-output-toggle',
} as const

export async function sendMessage(page: Page, text: string): Promise<void> {
  await page.fill(SEL.chatInput, text)
  await page.locator(SEL.sendButton).click()
}

export async function waitForAssistantMessage(page: Page, timeout = 30000): Promise<Locator> {
  const bubble = page.locator(SEL.assistantBubble).last()
  await bubble.waitFor({ state: 'visible', timeout })
  return bubble
}

export async function waitForAnyToolCard(page: Page, timeout = 30000): Promise<Locator | null> {
  const card = page.locator(SEL.toolCard).last()
  try {
    await card.waitFor({ state: 'visible', timeout })
    return card
  } catch {
    return null
  }
}

export async function waitForApprovalModal(page: Page, timeout = 30000): Promise<Locator | null> {
  const modal = page.locator(SEL.approvalModal)
  try {
    await modal.waitFor({ state: 'visible', timeout })
    return modal
  } catch {
    return null
  }
}

export async function waitForStreamToEnd(page: Page, timeout = 50000): Promise<boolean> {
  try {
    await page.locator(SEL.chatInput).waitFor({ state: 'enabled', timeout })
    return true
  } catch {
    return false
  }
}

export async function isInputEnabled(page: Page): Promise<boolean> {
  return page.locator(SEL.chatInput).isEnabled()
}

export async function createSessionAndSend(page: Page, message: string): Promise<void> {
  await page.click(SEL.newSessionButton)
  await page.waitForTimeout(500)
  await page.fill(SEL.chatInput, message)
  await page.locator(SEL.sendButton).click()
}
