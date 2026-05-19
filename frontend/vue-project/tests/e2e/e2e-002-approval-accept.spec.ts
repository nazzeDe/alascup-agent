import { test, expect } from '@playwright/test'
import { SEL, sendMessage, waitForApprovalModal, waitForToolCard } from './fixtures'
import { mockSessions, mockApproval, mockChatTurn, SSE } from './helpers'

const CHAT_ID = 'e2e-002-chat'
const PLAN_MSG_ID = 'e2e-002-plan'
const DIAG_TOOL_ID = 'e2e-002-diag'
const EXEC_TOOL_ID = 'e2e-002-exec'
const REQUEST_ID = 'req-e2e-002'

test.describe('E2E-002 高风险操作审批通过', () => {
  test.beforeEach(async ({ page }) => {
    await mockSessions(page)
    await mockApproval(page)

    await mockChatTurn(page, [
      SSE.toolCall(CHAT_ID, DIAG_TOOL_ID, 'get_disk_usage', {}, true),
      SSE.toolResult(CHAT_ID, DIAG_TOOL_ID, 'get_disk_usage', 'SUCCEEDED', {
        disk_usage: 92,
        largest_dir: '/tmp/logs',
      }),
      SSE.assistant(CHAT_ID, PLAN_MSG_ID, '磁盘使用率 92%，建议清理 /tmp/logs。'),
      SSE.assistant(CHAT_ID, PLAN_MSG_ID, '确认后执行 delete_temp_files。'),
      SSE.done(CHAT_ID),
    ])

    await page.goto('/')
  })

  test('diagnostic tool card shows before approval', async ({ page }) => {
    await sendMessage(page, '清理磁盘空间')
    const toolCard = await waitForToolCard(page)
    await expect(toolCard.locator(SEL.toolName)).toHaveText('get_disk_usage')
    await expect(toolCard).toContainText('Completed')
  })

  test('confirm execution triggers approval modal then approve', async ({ page }) => {
    await sendMessage(page, '清理磁盘空间')
    await waitForToolCard(page)

    await mockChatTurn(page, [
      SSE.toolApprovalRequired(CHAT_ID, REQUEST_ID, 'delete_temp_files', { path: '/tmp/logs' }),
      SSE.toolCall(CHAT_ID, EXEC_TOOL_ID, 'delete_temp_files', { path: '/tmp/logs' }, false),
      SSE.toolResult(CHAT_ID, EXEC_TOOL_ID, 'delete_temp_files', 'SUCCEEDED', { deleted: '2.3GB' }),
      SSE.assistant(CHAT_ID, PLAN_MSG_ID, '已清理 2.3GB 临时文件。'),
      SSE.done(CHAT_ID),
    ])

    await sendMessage(page, '确认执行')

    const modal = await waitForApprovalModal(page)
    await expect(modal).toContainText('delete_temp_files')

    await page.click(SEL.approveButton)
    await expect(modal).not.toBeVisible()

    const toolCard = await waitForToolCard(page)
    await expect(toolCard).toContainText('Completed')
    await expect(toolCard).toContainText('delete_temp_files')
  })
})
