import type { Page, Route } from "@playwright/test";

interface SSEEvent {
  event: string;
  data: unknown;
}

/** Mock POST /api/chat to replay a canned SSE stream. */
export async function mockChatTurn(
  page: Page,
  events: SSEEvent[],
): Promise<void> {
  await page.route("**/api/chat", (route: Route) => {
    const body =
      events
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n`)
        .join("\n") + "\n";
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body,
    });
  });
}

/** Mock POST /api/chat to return an HTTP error. */
export async function mockChatTurnError(
  page: Page,
  status = 500,
): Promise<void> {
  await page.route("**/api/chat", (route: Route) => {
    route.fulfill({ status });
  });
}

/** Mock GET/POST /api/sessions. */
export async function mockSessions(
  page: Page,
  sessions: unknown[] = [],
): Promise<void> {
  await page.route("**/api/sessions", (route: Route) => {
    if (route.request().method() === "POST") {
      const newSession = {
        chat_id: crypto.randomUUID(),
        messages: [],
        executed_tool_list: [],
        timestamp: new Date().toISOString(),
      };
      route.fulfill({ status: 200, json: newSession });
      return;
    }
    route.fulfill({ status: 200, json: sessions });
  });
}

/** Mock GET /api/sessions/:chatId. */
export async function mockSessionDetail(
  page: Page,
  session: unknown = {},
): Promise<void> {
  await page.route(/\/api\/sessions\/.*/, (route: Route) => {
    route.fulfill({ status: 200, json: session });
  });
}

/** Mock POST /api/tool-requests/:requestId/approval. */
export async function mockApproval(page: Page): Promise<void> {
  await page.route(/\/api\/tool-requests\/.*\/approval/, (route: Route) => {
    route.fulfill({
      status: 200,
      json: { status: route.request().postDataJSON()?.approval_status },
    });
  });
}

/** Factory functions for SSE event payloads. */
export const SSE = {
  assistant(chatId: string, msgId: string, delta: string): SSEEvent {
    return {
      event: "assistant",
      data: { chat_id: chatId, message_id: msgId, delta },
    };
  },
  assistantDone(chatId: string, msgId: string): SSEEvent {
    return {
      event: "assistant_done",
      data: { chat_id: chatId, message_id: msgId },
    };
  },
  toolCall(
    chatId: string,
    msgId: string,
    toolName: string,
    params: Record<string, unknown>,
    isReadOnly = true,
  ): SSEEvent {
    return {
      event: "tool_call",
      data: {
        chat_id: chatId,
        call_id: msgId,
        tool_name: toolName,
        params,
        is_read_only: isReadOnly,
      },
    };
  },
  toolResult(
    chatId: string,
    msgId: string,
    toolName: string,
    status: "SUCCEEDED" | "FAILED",
    output?: Record<string, unknown>,
  ): SSEEvent {
    return {
      event: "tool_result",
      data: {
        chat_id: chatId,
        call_id: msgId,
        tool_name: toolName,
        execution_status: status,
        output,
      },
    };
  },
  toolApprovalRequired(
    chatId: string,
    requestId: string,
    toolName: string,
    params: Record<string, unknown>,
  ): SSEEvent {
    return {
      event: "tool_approval_required",
      data: {
        chat_id: chatId,
        request_id: requestId,
        call_id: requestId,
        tool_name: toolName,
        params,
        reason: "This tool modifies system state",
      },
    };
  },
  error(code: string, message: string): SSEEvent {
    return { event: "error", data: { code, message } };
  },
  done(chatId: string): SSEEvent {
    return { event: "done", data: { chat_id: chatId } };
  },
};
