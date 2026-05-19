import { test, expect } from '@playwright/test'
import { SEL, sendMessage, waitForApprovalModal } from './fixtures'
import { mockSessions, mockApproval, mockChatTurn, SSE } from './helpers'

const CHAT_ID = 'e2e-003-chat'
const REQUEST_ID = 'req-e2e-003'

test.describe('E2E-003 高风险操作审批拒绝', () => {
  test.beforeEach(async ({ page }) => {
    await mockSessions(page)
    await mockApproval(page)

    await mockChatTurn(page, [
      SSE.toolApprovalRequired(CHAT_ID, REQUEST_ID, 'restart_nginx', { service: 'nginx' }),
      SSE.done(CHAT_ID),
    ])

    await page.goto('/')
  })

  test('approval modal shows with tool name and params', async ({ page }) => {
    await sendMessage(page, '重启 nginx 服务')
    const modal = await waitForApprovalModal(page)
    await expect(modal).toContainText('restart_nginx')
    await expect(modal).toContainText('nginx')
  })

  test('reject with reason dismisses modal', async ({ page }) => {
    await sendMessage(page, '重启 nginx 服务')
    const modal = await waitForApprovalModal(page)

    await page.fill(SEL.rejectReasonInput, '暂不需要')
    await page.click(SEL.rejectButton)

    await expect(modal).not.toBeVisible()
  })

  test('reject without reason also dismisses modal', async ({ page }) => {
    await sendMessage(page, '重启 nginx 服务')
    const modal = await waitForApprovalModal(page)

    await page.click(SEL.rejectButton)
    await expect(modal).not.toBeVisible()
  })
})
