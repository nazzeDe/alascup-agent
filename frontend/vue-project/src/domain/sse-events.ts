// SSE event payloads (wire format DTOs — sent by the backend over EventSource)

export interface AssistantEvent {
  delta: string;
}

export interface AssistantDoneEvent {
}

export interface ReasoningEvent {
  delta: string;
}

export interface ThinkingDoneEvent {
}

export interface ToolCallEvent {
  call_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  is_read_only: boolean;
  server?: string;
}

export interface ToolResultEvent {
  call_id: string;
  execution_status: "SUCCEEDED" | "FAILED" | "REJECTED";
  output?: Record<string, unknown>;
  error?: { code?: number; message: string; data?: string };
  execution_time_ms?: number;
}

export interface ToolApprovalRequiredEvent {
  chat_id: string;
  request_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  reason: string;
  call_id: string;
}

export interface SessionInitEvent {
  chat_id: string;
}

export interface ErrorEvent {
  code: string;
  message: string;
}

export type SSEEventType =
  | "assistant"
  | "assistant_done"
  | "reasoning"
  | "thinking_done"
  | "tool_call"
  | "tool_result"
  | "tool_approval_required"
  | "session_init"
  | "error"
  | "done";

export type ChatStreamEvent =
  | { type: "assistant"; data: AssistantEvent }
  | { type: "assistant_done"; data: AssistantDoneEvent }
  | { type: "reasoning"; data: ReasoningEvent }
  | { type: "thinking_done"; data: ThinkingDoneEvent }
  | { type: "tool_call"; data: ToolCallEvent }
  | { type: "tool_result"; data: ToolResultEvent }
  | { type: "tool_approval_required"; data: ToolApprovalRequiredEvent }
  | { type: "session_init"; data: SessionInitEvent }
  | { type: "error"; data: ErrorEvent }
  | { type: "done"; data: {} };
