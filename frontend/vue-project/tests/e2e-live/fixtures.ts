import type { Page, Locator } from '@playwright/test'

/** CSS selectors matching the Vue components. */
export const SEL = {
  chatMessages: '[data-testid="chat-messages"], .chat-messages',
  chatInput: '[data-testid="chat-input"], .chat-view textarea',
  sendButton: '[data-testid="send-button"], .btn-send-btn',
  userBubble: '[data-testid="user-bubble"], .chat-bubble-user',
  assistantBubble: '[data-testid="assistant-bubble"], .chat-bubble-assistant',
  systemBubble: '[data-testid="system-bubble"], .chat-bubble-system',
  toolCard: '[data-testid="tool-card"], .tool-call-inline',
  toolName: '[data-testid="tool-name"], .tool-call-inline .fw-medium',
  approvalModal: '[data-testid="approval-inline"], .approval-inline',
  approveButton: '[data-testid="approval-approve-button"], .approval-inline .btn-success',
  rejectButton: '[data-testid="approval-reject-button"], .approval-inline .btn-danger',
  rejectReasonInput: '[data-testid="approval-reason-input"], .approval-inline input',
  sessionItem: '[data-testid="session-item"], .session-item',
  newSessionButton: '[data-testid="new-session-button"], .btn-new-session',
  emptyChat: '[data-testid="empty-chat"], .chat-messages .text-center.text-muted',
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
