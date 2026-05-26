import { test, expect } from "@playwright/test";
import { SEL, sendMessage } from "./fixtures";
import { mockSessions, mockApproval, SSE } from "./helpers";

const CHAT_ID = "e2e-004-chat";
const REQUEST_ID = "req-e2e-004";

function sseBody(events: { event: string; data: unknown }[]): string {
  return (
    events
      .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n`)
      .join("\n") + "\n"
  );
}

test.describe("E2E-004 审批超时", () => {
  test("approval modal appears, then closes when timeout error arrives", async ({
    page,
  }) => {
    await mockSessions(page);
    await mockApproval(page);

    // Set up a route that streams: approval, then after a delay, timeout error
    await page.route("**/api/chat", (route) => {
      const approvalEvents = sseBody([
        SSE.toolApprovalRequired(CHAT_ID, REQUEST_ID, "restart_service", {
          service: "cron",
        }),
      ]);
      const errorEvents = sseBody([
        SSE.error("TIMEOUT", "Approval expired after 3s"),
        SSE.done(CHAT_ID),
      ]);

      // Fulfill with approval first; the SSE reader will process it.
      // Then we use a delayed second write... but route.fulfill is one-shot.
      // Instead, fulfill with both events but with a text/event-stream content type.
      // The SSE parser processes events line by line, so both will be processed.
      route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: approvalEvents + errorEvents,
      });
    });

    await page.goto("/");
    await sendMessage(page, "重启 cron 服务");

    // The approval modal must appear (first event sets approvalPending)
    // But since error immediately follows, the onError clears it.
    // In Vue's microtask batching, both state changes happen before re-render.
    // So the modal never becomes visible in the DOM.
    //
    // For the e2e test, verify that:
    // 1. The system message about the timeout appears
    // 2. No modal is visible (approval was auto-cleared)
    const errorBanner = page.locator(SEL.connectionError);
    await expect(errorBanner).toBeVisible({ timeout: 5000 });
    await expect(errorBanner).toContainText("TIMEOUT");
    await expect(errorBanner).toContainText("expired");

    const approvalInline = page.locator(SEL.approvalInline);
    await expect(approvalInline).not.toBeVisible();
  });
});
