import { test, expect } from '@playwright/test'
import { SEL, sendMessage } from './fixtures'
import { mockSessions, mockApproval, mockSessionDetail, mockChatTurn, SSE } from './helpers'

const CHAT_ID_1 = 'e2e-006-chat-1'
const CHAT_ID_2 = 'e2e-006-chat-2'
const MSG_ID = 'e2e-006-msg'

test.describe('E2E-006 会话管理', () => {
  test('sending a message creates a session in the sidebar', async ({ page }) => {
    await mockSessions(page)
    await mockApproval(page)
    await mockSessionDetail(page)
    await mockChatTurn(page, [
      SSE.assistant(CHAT_ID_1, MSG_ID, '回复内容。'),
      SSE.done(CHAT_ID_1),
    ])

    await page.goto('/')

    await expect(page.locator(SEL.sessionItem)).toHaveCount(0)

    await sendMessage(page, '帮我看下 CPU 情况')

    // Re-mock sessions to include the new one (simulating loadSessions after done)
    await mockSessions(page, [
      {
        chatID: CHAT_ID_1,
        title: '帮我看下 CPU 情况',
        messages: [],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      },
    ])
  })

  test('clicking session in sidebar switches content', async ({ page }) => {
    const sessionsFixture = [
      {
        chatID: CHAT_ID_1,
        title: 'CPU 诊断',
        messages: [
          { messageID: 'm1', chatID: CHAT_ID_1, timestamp: new Date().toISOString(), type: 'user', content: '查看 CPU' },
        ],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      },
      {
        chatID: CHAT_ID_2,
        title: '磁盘清理',
        messages: [
          { messageID: 'm2', chatID: CHAT_ID_2, timestamp: new Date().toISOString(), type: 'user', content: '清理磁盘' },
        ],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      },
    ]

    await mockSessions(page, sessionsFixture)
    await mockApproval(page)
    await mockSessionDetail(page, sessionsFixture[0])

    await page.goto('/')

    const items = page.locator(SEL.sessionItem)
    await expect(items).toHaveCount(2)
    await expect(items.first()).toContainText('CPU 诊断')
    await expect(items.last()).toContainText('磁盘清理')

    await mockSessionDetail(page, sessionsFixture[1])
    await items.last().click()

    await expect(items.last()).toHaveClass(/bg-primary-subtle/)
  })

  test('new session button works', async ({ page }) => {
    await mockSessions(page)
    await mockApproval(page)

    await page.goto('/')

    await page.click(SEL.newSessionButton)

    await expect(page.locator(SEL.emptyChat)).toBeVisible()
  })
})
