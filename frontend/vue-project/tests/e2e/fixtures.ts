import type { Page, Locator } from '@playwright/test'

/** CSS selectors matching the Vue components. */
export const SEL = {
  chatMessages: '.chat-messages',
  chatInput: '.chat-view textarea',
  sendButton: '.btn-send',
  userBubble: '.chat-bubble-user',
  assistantBubble: '.chat-bubble-assistant',
  systemBubble: '.chat-bubble-system',
  toolInline: '.tool-call-inline',
  toolName: '.tool-call-inline .fw-medium',
  approvalInline: '.approval-inline',
  approveButton: '.approval-inline .btn-success',
  rejectButton: '.approval-inline .btn-danger',
  rejectReasonInput: '.approval-inline input',
  connectionError: '.connection-error',
  sessionItem: '.session-item',
  newSessionButton: '.btn-new-session',
  emptyChat: '.chat-messages .text-center.text-muted',
  toolOutputToggle: '.tool-output-toggle',
} as const

export async function sendMessage(page: Page, text: string): Promise<void> {
  await page.fill(SEL.chatInput, text)
  await page.click(SEL.sendButton)
}

export async function waitForAssistantMessage(page: Page, timeout = 15000): Promise<Locator> {
  const bubble = page.locator(SEL.assistantBubble).last()
  await bubble.waitFor({ state: 'visible', timeout })
  return bubble
}

export async function waitForToolCard(page: Page, timeout = 10000): Promise<Locator> {
  const card = page.locator(SEL.toolCard).last()
  await card.waitFor({ state: 'visible', timeout })
  return card
}

export async function waitForApprovalModal(page: Page, timeout = 10000): Promise<Locator> {
  const modal = page.locator(SEL.approvalModal)
  await modal.waitFor({ state: 'visible', timeout })
  return modal
}

export async function isInputEnabled(page: Page): Promise<boolean> {
  return page.locator(SEL.chatInput).isEnabled()
}
