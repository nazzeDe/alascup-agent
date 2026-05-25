# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: e2e-live-001-chat-flow.spec.ts >> E2E-Live-001: Chat Flow >> input re-enables after stream ends
- Location: tests/e2e-live/e2e-live-001-chat-flow.spec.ts:23:3

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
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
            - generic [ref=e14]: 2114ea20
            - generic [ref=e15]: 5/22/2026, 6:55:25 PM
          - generic [ref=e16] [cursor=pointer]:
            - generic [ref=e17]: 6a0c4c2d
            - generic [ref=e18]: 5/22/2026, 6:54:47 PM
          - generic [ref=e19] [cursor=pointer]:
            - generic [ref=e20]: 3225e9b9
            - generic [ref=e21]: 5/22/2026, 6:54:39 PM
          - generic [ref=e22] [cursor=pointer]:
            - generic [ref=e23]: bb09ad52
            - generic [ref=e24]: 5/22/2026, 6:54:39 PM
          - generic [ref=e25] [cursor=pointer]:
            - generic [ref=e26]: 446b4614
            - generic [ref=e27]: 5/22/2026, 6:53:10 PM
          - generic [ref=e28] [cursor=pointer]:
            - generic [ref=e29]: e8e18e0c
            - generic [ref=e30]: 5/22/2026, 6:50:59 PM
          - generic [ref=e31] [cursor=pointer]:
            - generic [ref=e32]: 815ed2b0
            - generic [ref=e33]: 5/22/2026, 6:05:28 PM
          - generic [ref=e34] [cursor=pointer]:
            - generic [ref=e35]: 0d5061d7
            - generic [ref=e36]: 5/22/2026, 6:05:25 PM
          - generic [ref=e37] [cursor=pointer]:
            - generic [ref=e38]: f3805b25
            - generic [ref=e39]: 5/22/2026, 5:41:20 PM
          - generic [ref=e40] [cursor=pointer]:
            - generic [ref=e41]: daf3db12
            - generic [ref=e42]: 5/22/2026, 5:38:45 PM
          - generic [ref=e43] [cursor=pointer]:
            - generic [ref=e44]: 911dc551
            - generic [ref=e45]: 5/22/2026, 4:12:42 PM
          - generic [ref=e46] [cursor=pointer]:
            - generic [ref=e47]: 11ea62df
            - generic [ref=e48]: 5/22/2026, 4:05:30 PM
          - generic [ref=e49] [cursor=pointer]:
            - generic [ref=e50]: "69233376"
            - generic [ref=e51]: 5/22/2026, 4:05:20 PM
          - generic [ref=e52] [cursor=pointer]:
            - generic [ref=e53]: 00108ab7
            - generic [ref=e54]: 5/22/2026, 4:05:20 PM
          - generic [ref=e55] [cursor=pointer]:
            - generic [ref=e56]: 0e81e37d
            - generic [ref=e57]: 5/22/2026, 4:04:08 PM
          - generic [ref=e58] [cursor=pointer]:
            - generic [ref=e59]: 3f5cbf8b
            - generic [ref=e60]: 5/22/2026, 4:03:23 PM
          - generic [ref=e61] [cursor=pointer]:
            - generic [ref=e62]: 5da8faa6
            - generic [ref=e63]: 5/22/2026, 4:03:23 PM
          - generic [ref=e64] [cursor=pointer]:
            - generic [ref=e65]: "29766370"
            - generic [ref=e66]: 5/22/2026, 4:02:51 PM
          - generic [ref=e67] [cursor=pointer]:
            - generic [ref=e68]: 81dfbe46
            - generic [ref=e69]: 5/22/2026, 9:54:43 AM
      - generic [ref=e71]:
        - generic [ref=e74]: Reply with just the word OK
        - generic [ref=e75]:
          - status [ref=e76]:
            - generic [ref=e77]: Loading...
          - generic [ref=e78]: AI is responding...
          - button "Stop" [ref=e79] [cursor=pointer]
        - generic [ref=e81]:
          - textbox "Type your message... (Enter to send, Shift+Enter for newline)" [disabled] [ref=e82]
          - button "Send" [disabled]
  - generic [ref=e83]:
    - generic "Toggle devtools panel" [ref=e84] [cursor=pointer]:
      - img [ref=e85]
    - generic "Toggle Component Inspector" [ref=e90] [cursor=pointer]:
      - img [ref=e91]
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test'
  2  | import { SEL, sendMessage, waitForAssistantMessage, waitForStreamToEnd, createSessionAndSend } from './fixtures'
  3  | 
  4  | test.describe('E2E-Live-001: Chat Flow', () => {
  5  |   test('page loads with required UI elements', async ({ page }) => {
  6  |     await page.goto('/')
  7  |     await expect(page.locator(SEL.chatInput)).toBeVisible()
  8  |     await expect(page.locator(SEL.newSessionButton)).toBeVisible()
  9  |   })
  10 | 
  11 |   test('create session and send message triggers SSE stream', async ({ page }) => {
  12 |     await page.goto('/')
  13 |     await createSessionAndSend(page, 'Hello, what can you do?')
  14 | 
  15 |     // user message bubble appears immediately
  16 |     await expect(page.locator(SEL.userBubble).last()).toBeVisible({ timeout: 5000 })
  17 | 
  18 |     // assistant reply appears (real LLM may take time)
  19 |     const assistantBubble = await waitForAssistantMessage(page, 45000)
  20 |     expect(assistantBubble).not.toBeNull()
  21 |   })
  22 | 
  23 |   test('input re-enables after stream ends', async ({ page }) => {
  24 |     await page.goto('/')
  25 |     await createSessionAndSend(page, 'Reply with just the word OK')
  26 | 
  27 |     const ended = await waitForStreamToEnd(page, 45000)
> 28 |     expect(ended).toBe(true)
     |                   ^ Error: expect(received).toBe(expected) // Object.is equality
  29 |   })
  30 | 
  31 |   test('send button is disabled while streaming', async ({ page }) => {
  32 |     await page.goto('/')
  33 |     await sendMessage(page, 'List available tools and explain each one')
  34 | 
  35 |     // input should be disabled shortly after sending
  36 |     await page.waitForTimeout(500)
  37 |     const enabled = await page.locator(SEL.chatInput).isEnabled()
  38 |     expect(enabled).toBe(false)
  39 |   })
  40 | })
  41 | 
```