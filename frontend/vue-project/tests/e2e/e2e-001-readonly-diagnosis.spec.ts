import { test, expect } from '@playwright/test'
import { SEL, sendMessage, waitForAssistantMessage, waitForToolCard } from './fixtures'
import { mockSessions, mockApproval, mockChatTurn, SSE } from './helpers'

const CHAT_ID = 'e2e-001-chat'
const MSG_ID = 'e2e-001-msg'
const TOOL_MSG_ID = 'e2e-001-tool'

test.describe('E2E-001 只读诊断', () => {
  test.beforeEach(async ({ page }) => {
    await mockSessions(page)
    await mockApproval(page)
    await mockChatTurn(page, [
      SSE.toolCall(CHAT_ID, TOOL_MSG_ID, 'get_cpu_info', {}, true),
      SSE.toolResult(CHAT_ID, TOOL_MSG_ID, 'get_cpu_info', 'SUCCEEDED', {
        cpu_percent: 85,
        cores: 4,
      }),
      SSE.assistant(CHAT_ID, MSG_ID, '当前 CPU 占用 85%，4 核处理器。'),
      SSE.assistant(CHAT_ID, MSG_ID, '建议检查占用最高的进程。'),
      SSE.done(CHAT_ID),
    ])
    await page.goto('/')
  })

  test('page loads with chat interface', async ({ page }) => {
    await expect(page.locator(SEL.chatInput)).toBeVisible()
    await expect(page.locator(SEL.chatInput)).toBeEnabled()
    await expect(page.locator(SEL.emptyChat)).toBeVisible()
  })

  test('sending a message shows it in the list', async ({ page }) => {
    await sendMessage(page, '查看当前系统 CPU 占用')
    const userBubble = page.locator(SEL.userBubble).last()
    await expect(userBubble).toBeVisible()
    await expect(userBubble).toHaveText('查看当前系统 CPU 占用')
  })

  test('get_cpu_info tool card appears and shows completed status', async ({ page }) => {
    await sendMessage(page, '查看当前系统 CPU 占用')
    const toolCard = await waitForToolCard(page)
    await expect(toolCard.locator(SEL.toolName)).toHaveText('get_cpu_info')
    await expect(toolCard).toContainText('Completed')
  })

  test('assistant message contains CPU analysis', async ({ page }) => {
    await sendMessage(page, '查看当前系统 CPU 占用')
    const assistant = await waitForAssistantMessage(page)
    await expect(assistant).toContainText('CPU')
    await expect(assistant).toContainText('85%')
  })
})
