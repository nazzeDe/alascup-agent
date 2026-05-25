# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: e2e-live-001-chat-flow.spec.ts >> E2E-Live-001: Chat Flow >> create session and send message triggers SSE stream
- Location: tests/e2e-live/e2e-live-001-chat-flow.spec.ts:11:3

# Error details

```
TimeoutError: locator.waitFor: Timeout 45000ms exceeded.
Call log:
  - waiting for locator('.chat-bubble-assistant').last() to be visible

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e3]:
    - navigation [ref=e4]:
      - generic [ref=e5]: Alascup Agent
      - generic [ref=e6]: AI Ops Platform
    - generic [ref=e7]:
      - generic [ref=e9]:
        - button "+ New Session" [ref=e11] [cursor=pointer]
        - generic [ref=e12]:
          - generic [ref=e13] [cursor=pointer]:
            - generic [ref=e14]: 3225e9b9
            - generic [ref=e15]: 5/22/2026, 6:54:39 PM
          - generic [ref=e16] [cursor=pointer]:
            - generic [ref=e17]: 446b4614
            - generic [ref=e18]: 5/22/2026, 6:53:10 PM
          - generic [ref=e19] [cursor=pointer]:
            - generic [ref=e20]: e8e18e0c
            - generic [ref=e21]: 5/22/2026, 6:50:59 PM
          - generic [ref=e22] [cursor=pointer]:
            - generic [ref=e23]: 815ed2b0
            - generic [ref=e24]: 5/22/2026, 6:05:28 PM
          - generic [ref=e25] [cursor=pointer]:
            - generic [ref=e26]: 0d5061d7
            - generic [ref=e27]: 5/22/2026, 6:05:25 PM
          - generic [ref=e28] [cursor=pointer]:
            - generic [ref=e29]: f3805b25
            - generic [ref=e30]: 5/22/2026, 5:41:20 PM
          - generic [ref=e31] [cursor=pointer]:
            - generic [ref=e32]: daf3db12
            - generic [ref=e33]: 5/22/2026, 5:38:45 PM
          - generic [ref=e34] [cursor=pointer]:
            - generic [ref=e35]: 911dc551
            - generic [ref=e36]: 5/22/2026, 4:12:42 PM
          - generic [ref=e37] [cursor=pointer]:
            - generic [ref=e38]: 11ea62df
            - generic [ref=e39]: 5/22/2026, 4:05:30 PM
          - generic [ref=e40] [cursor=pointer]:
            - generic [ref=e41]: "69233376"
            - generic [ref=e42]: 5/22/2026, 4:05:20 PM
          - generic [ref=e43] [cursor=pointer]:
            - generic [ref=e44]: 00108ab7
            - generic [ref=e45]: 5/22/2026, 4:05:20 PM
          - generic [ref=e46] [cursor=pointer]:
            - generic [ref=e47]: 0e81e37d
            - generic [ref=e48]: 5/22/2026, 4:04:08 PM
          - generic [ref=e49] [cursor=pointer]:
            - generic [ref=e50]: 3f5cbf8b
            - generic [ref=e51]: 5/22/2026, 4:03:23 PM
          - generic [ref=e52] [cursor=pointer]:
            - generic [ref=e53]: 5da8faa6
            - generic [ref=e54]: 5/22/2026, 4:03:23 PM
          - generic [ref=e55] [cursor=pointer]:
            - generic [ref=e56]: "29766370"
            - generic [ref=e57]: 5/22/2026, 4:02:51 PM
          - generic [ref=e58] [cursor=pointer]:
            - generic [ref=e59]: 81dfbe46
            - generic [ref=e60]: 5/22/2026, 9:54:43 AM
      - generic [ref=e62]:
        - generic [ref=e65]: Hello, what can you do?
        - generic [ref=e67]:
          - textbox "Type your message... (Enter to send, Shift+Enter for newline)" [ref=e68]
          - button "Send" [disabled]
  - generic [ref=e69]:
    - generic "Toggle devtools panel" [ref=e70] [cursor=pointer]:
      - img [ref=e71]
    - generic "Toggle Component Inspector" [ref=e76] [cursor=pointer]:
      - img [ref=e77]
```

# Test source

```ts
  1  | import type { Page, Locator } from '@playwright/test'
  2  | 
  3  | /** CSS selectors matching the Vue components. */
  4  | export const SEL = {
  5  |   chatMessages: '.chat-messages',
  6  |   chatInput: '.chat-view textarea',
  7  |   sendButton: '.btn-send',
  8  |   userBubble: '.chat-bubble-user',
  9  |   assistantBubble: '.chat-bubble-assistant',
  10 |   systemBubble: '.chat-bubble-system',
  11 |   toolCard: '.tool-call-card',
  12 |   toolName: '.tool-name',
  13 |   approvalModal: '.modal',
  14 |   approveButton: '.btn-approve',
  15 |   rejectButton: '.btn-reject',
  16 |   rejectReasonInput: '.modal-body textarea',
  17 |   sessionItem: '.session-item',
  18 |   newSessionButton: '.btn-new-session',
  19 |   emptyChat: '.chat-messages .text-center.text-muted',
  20 |   toolOutputToggle: '.tool-output-toggle',
  21 | } as const
  22 | 
  23 | export async function sendMessage(page: Page, text: string): Promise<void> {
  24 |   await page.fill(SEL.chatInput, text)
  25 |   await page.click(SEL.sendButton)
  26 | }
  27 | 
  28 | export async function waitForAssistantMessage(page: Page, timeout = 30000): Promise<Locator> {
  29 |   const bubble = page.locator(SEL.assistantBubble).last()
> 30 |   await bubble.waitFor({ state: 'visible', timeout })
     |                ^ TimeoutError: locator.waitFor: Timeout 45000ms exceeded.
  31 |   return bubble
  32 | }
  33 | 
  34 | export async function waitForAnyToolCard(page: Page, timeout = 30000): Promise<Locator | null> {
  35 |   const card = page.locator(SEL.toolCard).last()
  36 |   try {
  37 |     await card.waitFor({ state: 'visible', timeout })
  38 |     return card
  39 |   } catch {
  40 |     return null
  41 |   }
  42 | }
  43 | 
  44 | export async function waitForApprovalModal(page: Page, timeout = 30000): Promise<Locator | null> {
  45 |   const modal = page.locator(SEL.approvalModal)
  46 |   try {
  47 |     await modal.waitFor({ state: 'visible', timeout })
  48 |     return modal
  49 |   } catch {
  50 |     return null
  51 |   }
  52 | }
  53 | 
  54 | export async function waitForStreamToEnd(page: Page, timeout = 50000): Promise<boolean> {
  55 |   try {
  56 |     await page.locator(SEL.chatInput).waitFor({ state: 'enabled', timeout })
  57 |     return true
  58 |   } catch {
  59 |     return false
  60 |   }
  61 | }
  62 | 
  63 | export async function isInputEnabled(page: Page): Promise<boolean> {
  64 |   return page.locator(SEL.chatInput).isEnabled()
  65 | }
  66 | 
  67 | export async function createSessionAndSend(page: Page, message: string): Promise<void> {
  68 |   await page.click(SEL.newSessionButton)
  69 |   await page.waitForTimeout(500)
  70 |   await page.fill(SEL.chatInput, message)
  71 |   await page.click(SEL.sendButton)
  72 | }
  73 | 
```