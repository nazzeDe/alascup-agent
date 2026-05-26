import { test, expect } from "@playwright/test";
import { SEL, sendMessage, isInputEnabled } from "./fixtures";
import { mockSessions, mockApproval, mockChatTurnError, SSE } from "./helpers";

const CHAT_ID = "e2e-005-chat";
const MSG_ID = "e2e-005-msg";

test.describe("E2E-005 SSE 错误恢复", () => {
  test("network error shows system message and input stays usable", async ({
    page,
  }) => {
    await mockSessions(page);
    await mockApproval(page);
    await mockChatTurnError(page, 500);

    await page.goto("/");

    await sendMessage(page, "查看系统状态");

    const errorBanner = page.locator(SEL.connectionError);
    await expect(errorBanner).toBeVisible({ timeout: 5000 });
    await expect(errorBanner).toContainText("Error");

    expect(await isInputEnabled(page)).toBe(true);
  });

  test("can send new message after SSE error", async ({ page }) => {
    await mockSessions(page);
    await mockApproval(page);

    let firstRequest = true;
    await page.route("**/api/chat", (route) => {
      if (firstRequest) {
        firstRequest = false;
        route.fulfill({ status: 500 });
      } else {
        const body =
          [
            SSE.assistant(CHAT_ID, MSG_ID, "系统已恢复，正常运行。"),
            SSE.done(CHAT_ID),
          ]
            .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n`)
            .join("\n") + "\n";
        route.fulfill({
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
          body,
        });
      }
    });

    await page.goto("/");

    await sendMessage(page, "查看系统状态");
    const errorBanner = page.locator(SEL.connectionError);
    await expect(errorBanner).toBeVisible({ timeout: 5000 });

    await sendMessage(page, "继续诊断");
    const assistantBubble = page.locator(SEL.assistantBubble);
    await expect(assistantBubble).toBeVisible({ timeout: 5000 });
    await expect(assistantBubble).toContainText("已恢复");
  });
});
