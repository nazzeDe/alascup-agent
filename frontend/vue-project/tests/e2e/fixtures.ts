import type { Page, Locator } from '@playwright/test'

/** CSS selectors matching the Vue components. */
export const SEL = {
  chatMessages: '[data-testid="chat-messages"], .chat-messages',
  chatInput: '[data-testid="chat-input"], .chat-view textarea',
  sendButton: '[data-testid="send-button"], .btn-send-btn',
  userBubble: '[data-testid="user-bubble"], .chat-bubble-user',
  assistantBubble: '[data-testid="assistant-bubble"], .chat-bubble-assistant',
  systemBubble: '[data-testid="system-bubble"], .chat-bubble-system',
  toolInline: '[data-testid="tool-card"], .tool-call-inline',
  toolCard: '[data-testid="tool-card"], .tool-call-inline',
  toolName: '[data-testid="tool-name"], .fw-medium',
  approvalInline: '[data-testid="approval-inline"], .approval-inline',
  approvalModal: '[data-testid="approval-inline"], .approval-inline',
  approveButton: '[data-testid="approval-approve-button"], .approval-inline .btn-success',
  rejectButton: '[data-testid="approval-reject-button"], .approval-inline .btn-danger',
  rejectReasonInput: '[data-testid="approval-reason-input"], .approval-inline input',
  connectionError: '[data-testid="connection-error"], .connection-error',
  sessionItem: '[data-testid="session-item"], .session-item',
  sessionItemActive: '[data-testid="session-item"].active, .session-item.active',
  newSessionButton: '[data-testid="new-session-button"], .btn-new-session',
  emptyChat: '[data-testid="empty-chat"], .chat-messages .text-center.text-muted',
  toolOutputToggle: '.tool-output-toggle',
} as const

export async function sendMessage(page: Page, text: string): Promise<void> {
  await page.fill(SEL.chatInput, text)
  await page.locator(SEL.sendButton).click()
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
